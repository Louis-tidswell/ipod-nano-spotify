/* Spotify, Bluetooth and playlist controller for the 240 x 432 Nano screen. */
#include "hb_sdk.h"
#include "lvgl/lvgl.h"
#include "src/misc/cache/instance/lv_image_cache.h"

#define NANO_TO_PI_ADDR 0x09122000u
#define PI_TO_NANO_ADDR 0x09122200u
#define NANO_TO_PI_MAGIC 0x4932504eu
#define PI_TO_NANO_MAGIC 0x4e324950u
#define PROTOCOL_VERSION 4u
#define CHECKSUM_SALT 0xa5a55a5au
#define LINK_TIMEOUT_MS 3000u
#define ART_SIDE 96u
#define ART_BYTES (ART_SIDE * ART_SIDE * 2u)
#define ART_CHUNKS (ART_BYTES / 256u)
#define FLAG_PLAYER_READY (1u << 0)
#define FLAG_PLAYING (1u << 1)

/* Layout matches pi/spotify_bridge.py; all status variants remain 512 bytes. */
typedef struct {
    uint32_t magic, version, seq, command, value, nonce, checksum;
    char payload[64];
    uint8_t reserved[420];
} command_page_t;
typedef struct { char address[18]; uint16_t flags; char name[44]; } bt_row_t;
typedef struct { char uri[40], name[52]; uint32_t length; } playlist_row_t;
typedef struct {
    uint32_t magic, version, generation, ack, flags;
    uint32_t position_ms, duration_ms, volume_percent, error_code, nonce, checksum;
    char track[64], artist[64], message[64];
    uint32_t kind, extra_flags, total, page, count;
    union {
        struct { char message[64]; bt_row_t rows[3]; } bt;
        struct { char message[64]; playlist_row_t rows[2]; } library;
        uint8_t pixels[256];
    } data;
} status_page_t;
_Static_assert(sizeof(command_page_t) == 512, "Command page size");
_Static_assert(sizeof(status_page_t) == 512, "Status page size");

static volatile command_page_t *const s_out = (volatile command_page_t *)NANO_TO_PI_ADDR;
static volatile status_page_t *const s_in = (volatile status_page_t *)PI_TO_NANO_ADDR;
static status_page_t s_snapshot, s_bt, s_library;
static uint32_t s_nonce, s_seq, s_last_generation, s_last_pi_ms;
static int s_connected, s_tab, s_bt_view;
static char s_bt_address[18], s_bt_name[44];
static char s_config_url[64] = "Checking Pi network...";
static lv_obj_t *s_config_label;
static lv_obj_t *s_content, *s_status, *s_tabs[3];
static lv_obj_t *s_track, *s_artist, *s_progress, *s_time, *s_play, *s_volume, *s_cover, *s_cover_hint;
static lv_obj_t *s_pane_message, *s_scan, *s_page_label, *s_rows[3], *s_actions[3], *s_pager[2];
static uint8_t s_art[ART_BYTES];
static uint32_t s_art_id, s_art_seen[3];
static int s_art_ready, s_art_acknowledged;
static lv_image_dsc_t s_art_image;
static struct { uint32_t command; int32_t value; char payload[64]; } s_queue[8];
static int s_queue_head, s_queue_count;

static void barrier(void) {
#ifndef NANO_UI_TEST
    __asm__ volatile("dmb" ::: "memory");
#endif
}
static void copy_text(char *dst, const char *src, int size) {
    int i; for (i = 0; i < size - 1 && src[i]; i++) dst[i] = src[i]; dst[i] = 0;
}
static char *append_text(char *dst, const char *src) { while (*src) *dst++ = *src++; return dst; }
static char *append_u32(char *dst, uint32_t n) {
    char reverse[11]; int count = 0; do { reverse[count++] = '0' + n % 10u; n /= 10u; } while (n);
    while (count) *dst++ = reverse[--count];
    return dst;
}
static uint32_t page_checksum(const uint32_t *words, int skip) {
    uint32_t result = CHECKSUM_SALT; int i;
    for (i = 0; i < 128; i++) if (i != skip) result ^= words[i];
    return result;
}
static void send_next(void) {
    command_page_t page = {0}; int i, index;
    if (!s_queue_count || (s_seq && s_snapshot.ack != s_seq)) return;
    index = s_queue_head;
    page.magic = NANO_TO_PI_MAGIC; page.version = PROTOCOL_VERSION; page.seq = ++s_seq;
    page.command = s_queue[index].command; page.value = (uint32_t)s_queue[index].value; page.nonce = s_nonce;
    copy_text(page.payload, s_queue[index].payload, sizeof(page.payload));
    page.checksum = page_checksum((const uint32_t *)&page, 6);
    s_out->magic = 0; barrier();
    for (i = 1; i < 128; i++) ((volatile uint32_t *)s_out)[i] = ((const uint32_t *)&page)[i];
    barrier(); s_out->magic = NANO_TO_PI_MAGIC; barrier();
    s_queue_head = (s_queue_head + 1) % 8; s_queue_count--;
}
static void publish_address(uint32_t command, int32_t value, const char *payload) {
    int index;
    if (s_queue_count >= 8) return;
    index = (s_queue_head + s_queue_count) % 8;
    s_queue[index].command = command; s_queue[index].value = value;
    copy_text(s_queue[index].payload, payload ? payload : "", 64);
    s_queue_count++; send_next();
}
static void publish(uint32_t command, int32_t value) { publish_address(command, value, NULL); }
static void enabled(lv_obj_t *obj, int on) {
    if (!obj) return;
    if (on) lv_obj_remove_state(obj, LV_STATE_DISABLED); else lv_obj_add_state(obj, LV_STATE_DISABLED);
}
static lv_obj_t *label(const char *text, int x, int y, int width, int large) {
    lv_obj_t *l = lv_label_create(s_content);
    lv_label_set_text(l, text); lv_obj_set_pos(l, x, y); lv_obj_set_width(l, width);
    lv_label_set_long_mode(l, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_color(l, lv_color_hex(0xcbd3db), 0);
    lv_obj_set_style_text_font(l, large ? &lv_font_montserrat_20 : &lv_font_montserrat_14, 0);
    return l;
}
static lv_obj_t *button_on(lv_obj_t *parent, const char *text, int x, int y, int w, int h, lv_event_cb_t cb, intptr_t data) {
    lv_obj_t *b = lv_button_create(parent); lv_obj_set_size(b, w, h);
    /* Explicit top-left alignment: a bottom-aligned button plus set_pos goes off-screen. */
    lv_obj_align(b, LV_ALIGN_TOP_LEFT, x, y);
    lv_obj_set_style_pad_all(b, 4, 0); lv_obj_set_style_radius(b, 10, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(0x202832), 0);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, (void *)data);
    lv_obj_t *l = lv_label_create(b); lv_label_set_text(l, text); lv_obj_set_width(l, w - 12);
    lv_label_set_long_mode(l, LV_LABEL_LONG_DOT); lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_color(l, lv_color_hex(0xffffff), 0);
    lv_obj_set_style_text_font(l, &lv_font_montserrat_14, 0); lv_obj_center(l); return b;
}
static lv_obj_t *button(const char *text, int x, int y, int w, int h, lv_event_cb_t cb, intptr_t data) {
    return button_on(s_content, text, x, y, w, h, cb, data);
}
static void clear_content(void) {
    int i;
    s_track = s_artist = s_progress = s_time = s_play = s_volume = s_cover = s_cover_hint = NULL;
    s_pane_message = s_scan = s_page_label = NULL;
    s_config_label = NULL;
    for (i = 0; i < 3; i++) { s_rows[i] = NULL; s_actions[i] = NULL; }
    for (i = 0; i < 2; i++) s_pager[i] = NULL;
    lv_obj_clean(s_content); lv_obj_scroll_to_y(s_content, 0, LV_ANIM_OFF);
    lv_obj_clear_flag(s_content, LV_OBJ_FLAG_SCROLLABLE);
}
static void update_view(void);
static void build_bluetooth(void);
static void build_library(void);
static void build_now_playing(void);
static void on_command(lv_event_t *e) { publish((uint32_t)(uintptr_t)lv_event_get_user_data(e), 0); }
static void on_volume(lv_event_t *e) { publish(4, (int32_t)(intptr_t)lv_event_get_user_data(e)); }

static void build_now_playing(void) {
    clear_content();
    s_cover_hint = label("Loading cover...", 58, 44, 124, 0);
    s_cover = lv_image_create(s_content); lv_obj_set_pos(s_cover, 72, 6);
    if (s_art_ready) lv_image_set_src(s_cover, &s_art_image);
    else lv_obj_add_flag(s_cover, LV_OBJ_FLAG_HIDDEN);
    s_track = label("Nothing playing", 12, 110, 216, 1);
    lv_label_set_long_mode(s_track, LV_LABEL_LONG_SCROLL_CIRCULAR);
    s_artist = label("Select iPod Nano in Spotify", 12, 140, 216, 0);
    lv_label_set_long_mode(s_artist, LV_LABEL_LONG_SCROLL_CIRCULAR);
    s_progress = lv_bar_create(s_content); lv_obj_set_size(s_progress, 216, 6); lv_obj_set_pos(s_progress, 12, 170);
    lv_bar_set_range(s_progress, 0, 1000);
    lv_obj_set_style_bg_color(s_progress, lv_color_hex(0x303944), LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_progress, lv_color_hex(0x1db954), LV_PART_INDICATOR);
    s_time = label("0:00 / 0:00", 12, 184, 216, 0);
    button("PREV", 12, 212, 64, 48, on_command, 2);
    s_play = button("PLAY", 84, 212, 72, 48, on_command, 1);
    lv_obj_set_style_bg_color(s_play, lv_color_hex(0x1db954), 0);
    button("NEXT", 164, 212, 64, 48, on_command, 3);
    button("-", 12, 274, 54, 44, on_volume, -5);
    button("+", 174, 274, 54, 44, on_volume, 5);
    s_volume = label("VOL 0%", 76, 289, 90, 0);
    update_view();
}
static void on_bt_back(lv_event_t *e) { (void)e; s_bt_view = 0; build_bluetooth(); }
static void on_bt_screen(lv_event_t *e) { s_bt_view = (int)(intptr_t)lv_event_get_user_data(e); build_bluetooth(); }
static void on_bt_action(lv_event_t *e) {
    uint32_t command = (uint32_t)(uintptr_t)lv_event_get_user_data(e);
    if (command == 14 && s_bt_view != 2) { s_bt_view = 2; build_bluetooth(); return; }
    publish_address(command, 0, command == 11 ? NULL : s_bt_address);
    if (command == 14) { s_bt_view = 0; build_bluetooth(); }
    update_view();
}
static void on_bt_row(lv_event_t *e) {
    int row = (int)(intptr_t)lv_event_get_user_data(e);
    if (row >= (int)s_bt.count) return;
    copy_text(s_bt_address, s_bt.data.bt.rows[row].address, 18);
    copy_text(s_bt_name, s_bt.data.bt.rows[row].name, 44);
    s_bt_view = 1; build_bluetooth();
}
static void on_page(lv_event_t *e) {
    int delta = (int)(intptr_t)lv_event_get_user_data(e), page;
    if (s_tab == 2) {
        page = (int)s_bt.page + delta;
        if (page >= 0 && (uint32_t)page * 3 < s_bt.total) publish(10, page);
    } else {
        page = (int)s_library.page + delta;
        if (page >= 0 && (uint32_t)page * 2 < s_library.total) publish(20, page);
    }
    update_view();
}
static lv_obj_t *list_container(void) {
    lv_obj_t *list = lv_obj_create(s_content); lv_obj_set_pos(list, 12, 104); lv_obj_set_size(list, 216, 168);
    lv_obj_set_style_pad_all(list, 0, 0); lv_obj_set_style_border_width(list, 0, 0);
    lv_obj_set_style_bg_opa(list, LV_OPA_TRANSP, 0); lv_obj_set_scroll_dir(list, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(list, LV_SCROLLBAR_MODE_AUTO);
    return list;
}
static void pager(void) {
    s_pager[0] = button("<", 12, 280, 54, 44, on_page, -1);
    s_pager[1] = button(">", 174, 280, 54, 44, on_page, 1);
    s_page_label = label("0/0", 82, 294, 74, 0);
}
static void build_bluetooth(void) {
    int i; clear_content();
    if (s_bt_view == -1) {
        label("Config", 12, 8, 216, 1);
        s_config_label = label(s_config_url, 12, 40, 216, 0);
        lv_label_set_long_mode(s_config_label, LV_LABEL_LONG_WRAP);
        label("Open on the same Wi-Fi.", 12, 88, 216, 0);
        label("BLUETOOTH AUDIO", 12, 136, 216, 0);
        button("Speakers / headphones", 12, 160, 216, 48, on_bt_screen, 0);
        s_pane_message = label("", 12, 222, 216, 0);
        lv_label_set_long_mode(s_pane_message, LV_LABEL_LONG_WRAP);
    } else if (s_bt_view == 0) {
        label("BLUETOOTH AUDIO", 12, 4, 216, 0);
        button("Back", 12, 26, 62, 44, on_bt_screen, -1);
        s_scan = button("Scan", 82, 26, 146, 44, on_bt_action, 11);
        s_pane_message = label("Checking Bluetooth...", 12, 78, 216, 0);
        lv_obj_t *list = list_container();
        for (i = 0; i < 3; i++) s_rows[i] = button_on(list, "", 0, i * 58, 216, 52, on_bt_row, i);
        pager();
    } else {
        label(s_bt_name, 12, 8, 216, 1); label(s_bt_address, 12, 38, 216, 0);
        s_pane_message = label("", 12, 72, 216, 0);
        if (s_bt_view == 2) {
            lv_obj_t *l = label("Forget this speaker? You will need to pair it again.", 12, 112, 216, 0);
            lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
            s_actions[0] = button("Forget speaker", 12, 202, 216, 48, on_bt_action, 14);
        } else {
            s_actions[0] = button("Pair / Connect", 12, 116, 216, 48, on_bt_action, 12);
            s_actions[1] = button("Disconnect", 12, 172, 216, 48, on_bt_action, 13);
            s_actions[2] = button("Forget...", 12, 228, 216, 44, on_bt_action, 14);
        }
        button("Back", 12, 280, 216, 44, on_bt_back, 0);
    }
    update_view();
}
static void on_library_refresh(lv_event_t *e) { (void)e; publish(22, s_library.page); update_view(); }
static void on_playlist(lv_event_t *e) {
    int row = (int)(intptr_t)lv_event_get_user_data(e);
    if (row >= (int)s_library.count) return;
    publish_address(21, 0, s_library.data.library.rows[row].uri);
    update_view();
}
static void build_library(void) {
    int i; clear_content();
    label("YOUR PLAYLISTS", 12, 4, 216, 0);
    s_scan = button("Refresh playlists", 12, 26, 216, 44, on_library_refresh, 0);
    s_pane_message = label("Loading your playlists...", 12, 78, 216, 0);
    lv_obj_t *list = list_container();
    for (i = 0; i < 2; i++) s_rows[i] = button_on(list, "", 0, i * 80, 216, 72, on_playlist, i);
    pager(); update_view();
}
static void show_cover(void) {
    if (!s_cover) return;
    if (s_art_ready) {
        lv_image_set_src(s_cover, &s_art_image); lv_obj_remove_flag(s_cover, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_cover_hint, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_cover, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(s_cover_hint, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(s_cover_hint, s_art_id ? "Loading cover..." : "No cover art");
    }
}
static void update_view(void) {
    int i, ready = s_connected && s_snapshot.ack == s_seq && !s_queue_count;
    char text[100], *p;
    if (s_tab == 0 && s_track) {
        lv_label_set_text(s_track, s_snapshot.track[0] ? s_snapshot.track : "Nothing playing");
        lv_label_set_text(s_artist, s_snapshot.artist[0] ? s_snapshot.artist : "Select iPod Nano in Spotify");
        lv_label_set_text(lv_obj_get_child(s_play, 0), (s_snapshot.flags & FLAG_PLAYING) ? "PAUSE" : "PLAY");
        lv_bar_set_value(s_progress, s_snapshot.duration_ms ? (int)((s_snapshot.position_ms * 1000ull) / s_snapshot.duration_ms) : 0, LV_ANIM_OFF);
        p = append_u32(text, s_snapshot.position_ms / 60000); *p++ = ':';
        if (s_snapshot.position_ms / 1000 % 60 < 10) *p++ = '0';
        p = append_u32(p, s_snapshot.position_ms / 1000 % 60); p = append_text(p, " / ");
        p = append_u32(p, s_snapshot.duration_ms / 60000); *p++ = ':';
        if (s_snapshot.duration_ms / 1000 % 60 < 10) *p++ = '0';
        p = append_u32(p, s_snapshot.duration_ms / 1000 % 60); *p = 0; lv_label_set_text(s_time, text);
        p = append_text(text, "VOL "); p = append_u32(p, s_snapshot.volume_percent); *p++ = '%'; *p = 0;
        lv_label_set_text(s_volume, text); show_cover(); return;
    }
    if (!s_pane_message) return;
    status_page_t *pane = s_tab == 2 ? &s_bt : &s_library;
    int busy = s_tab == 2 ? !!(pane->extra_flags & 4) : !!(pane->extra_flags & 1);
    ready = ready && !busy;
    lv_label_set_text(s_pane_message, s_connected ? (pane->data.bt.message[0] ? pane->data.bt.message : "Loading...") : "Pi link offline");
    enabled(s_scan, ready);
    if (s_config_label) lv_label_set_text(s_config_label, s_config_url);
    if (s_tab == 2 && s_scan) lv_label_set_text(lv_obj_get_child(s_scan, 0), pane->extra_flags & 2 ? "Stop scan" : "Scan");
    for (i = 0; i < 3; i++) {
        enabled(s_actions[i], ready);
        if (!s_rows[i]) continue;
        if (i >= (int)pane->count) { lv_obj_add_flag(s_rows[i], LV_OBJ_FLAG_HIDDEN); continue; }
        lv_obj_remove_flag(s_rows[i], LV_OBJ_FLAG_HIDDEN);
        if (s_tab == 2) {
            bt_row_t *row = &pane->data.bt.rows[i]; p = append_text(text, row->name);
            p = append_text(p, row->flags & 4 ? " [audio]" : row->flags & 2 ? " [linked]" : row->flags & 1 ? " [saved]" : " [new]"); *p = 0;
        } else { copy_text(text, pane->data.library.rows[i].name, sizeof(text)); }
        lv_obj_t *l = lv_obj_get_child(s_rows[i], 0); lv_label_set_text(l, text);
        lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP); enabled(s_rows[i], ready);
    }
    if (s_page_label) {
        uint32_t size = s_tab == 2 ? 3 : 2;
        p = append_u32(text, pane->total ? pane->page + 1 : 0); *p++ = '/';
        p = append_u32(p, (pane->total + size - 1) / size); *p = 0; lv_label_set_text(s_page_label, text);
        enabled(s_pager[0], ready && pane->page > 0);
        enabled(s_pager[1], ready && (pane->page + 1) * size < pane->total);
    }
}
static void on_tab(lv_event_t *e) {
    int i; s_tab = (int)(intptr_t)lv_event_get_user_data(e);
    publish(30, s_tab);
    for (i = 0; i < 3; i++) lv_obj_set_style_bg_color(s_tabs[i], lv_color_hex(i == s_tab ? 0x1db954 : 0x161d25), 0);
    if (s_tab == 0) build_now_playing();
    else if (s_tab == 1) { publish(20, 0); build_library(); }
    else { publish(10, 0); s_bt_view = -1; build_bluetooth(); }
}
static void accept_art(status_page_t *page) {
    uint32_t i;
    if (page->total != ART_BYTES || page->page >= ART_CHUNKS || page->count != 256 || !page->extra_flags) {
        if (!page->total) { s_art_ready = 0; s_art_id = 0; }
        return;
    }
    if (s_art_id != page->extra_flags) {
        s_art_id = page->extra_flags; s_art_ready = s_art_acknowledged = 0;
        for (i = 0; i < 3; i++) s_art_seen[i] = 0;
        lv_image_cache_drop(&s_art_image);
    }
    for (i = 0; i < 256; i++) s_art[page->page * 256 + i] = page->data.pixels[i];
    s_art_seen[page->page / 32] |= 1u << (page->page % 32);
    if (s_art_seen[0] == 0xffffffffu && s_art_seen[1] == 0xffffffffu && s_art_seen[2] == 0xffu) {
        s_art_ready = 1;
        s_art_image.header.magic = LV_IMAGE_HEADER_MAGIC; s_art_image.header.cf = LV_COLOR_FORMAT_RGB565;
        s_art_image.header.w = ART_SIDE; s_art_image.header.h = ART_SIDE; s_art_image.header.stride = ART_SIDE * 2;
        s_art_image.data_size = ART_BYTES; s_art_image.data = s_art;
        if (!s_art_acknowledged && s_queue_count < 8) { publish(31, (int32_t)s_art_id); s_art_acknowledged = 1; }
    }
}
static void update_status(void) {
    status_page_t page; int i;
    for (i = 0; i < 128; i++) ((uint32_t *)&page)[i] = ((volatile uint32_t *)s_in)[i];
    barrier();
    if (page.magic != s_in->magic || page.generation != s_in->generation || page.magic != PI_TO_NANO_MAGIC ||
        page.version != PROTOCOL_VERSION || page.nonce != s_nonce || page.generation == s_last_generation ||
        page.checksum != page_checksum((const uint32_t *)&page, 10)) return;
    page.track[63] = page.artist[63] = page.message[63] = 0;
    if (page.kind == 1) {
        if (page.count > 3) return;
        page.data.bt.message[63] = 0;
        for (i = 0; i < 3; i++) { page.data.bt.rows[i].name[43] = 0; page.data.bt.rows[i].address[17] = 0; }
        s_bt = page;
    } else if (page.kind == 2) {
        if (page.count > 2) return;
        page.data.library.message[63] = 0;
        for (i = 0; i < 2; i++) { page.data.library.rows[i].name[51] = 0; page.data.library.rows[i].uri[39] = 0; }
        s_library = page;
    } else if (page.kind == 3) accept_art(&page);
    else if (page.kind == 4) { page.data.bt.message[63] = 0; copy_text(s_config_url, page.data.bt.message, 64); }
    else return;
    s_snapshot = page; s_connected = 1; s_last_generation = page.generation; s_last_pi_ms = hb_time_uptime_ms();
    lv_label_set_text(s_status, page.message);
    lv_obj_set_style_text_color(s_status, lv_color_hex(page.flags & FLAG_PLAYER_READY ? 0x1db954 : 0xf5a623), 0);
    send_next(); update_view();
}
static void on_tick(void) {
    update_status();
    if (s_connected && hb_time_uptime_ms() - s_last_pi_ms > LINK_TIMEOUT_MS) {
        s_connected = 0; lv_label_set_text(s_status, "Pi link offline"); update_view();
    }
}
HB_APP_ENTRY(payload_entry) {
    lv_obj_t *screen = lv_screen_active(); int i;
    const char *tabs[] = {"NOW", "LIBRARY", "SYSTEM"};
    s_nonce = hb_time_uptime_ms() ^ 0x73706f74u;
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x0b0f14), 0);
    lv_obj_clear_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *title = lv_label_create(screen); lv_label_set_text(title, "Spotify");
    lv_obj_set_pos(title, 12, 6); lv_obj_set_style_text_font(title, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(title, lv_color_hex(0xffffff), 0);
    s_status = lv_label_create(screen); lv_label_set_text(s_status, "Connecting to Pi...");
    lv_obj_set_pos(s_status, 12, 36); lv_obj_set_width(s_status, 216);
    lv_label_set_long_mode(s_status, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_font(s_status, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(s_status, lv_color_hex(0xf5a623), 0);
    s_content = lv_obj_create(screen); lv_obj_set_pos(s_content, 0, 56); lv_obj_set_size(s_content, 240, 332);
    lv_obj_set_style_bg_opa(s_content, LV_OPA_TRANSP, 0); lv_obj_set_style_border_width(s_content, 0, 0);
    lv_obj_set_style_pad_all(s_content, 0, 0); lv_obj_set_scroll_dir(s_content, LV_DIR_VER);
    for (i = 0; i < 3; i++) {
        s_tabs[i] = button_on(screen, tabs[i], i * 80, 388, 80, 44, on_tab, i);
        lv_obj_set_style_radius(s_tabs[i], 0, 0);
        lv_obj_set_style_bg_color(s_tabs[i], lv_color_hex(i == 0 ? 0x1db954 : 0x161d25), 0);
    }
    build_now_playing(); publish(0, 0); hb_lv_set_frame_cb(on_tick);
}
