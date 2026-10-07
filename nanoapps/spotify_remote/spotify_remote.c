/* Basic full-shell Spotify controller for iPod nano 7. */
#include "hb_sdk.h"
#include "lvgl/lvgl.h"

#define NANO_TO_PI_ADDR 0x09122000u
#define PI_TO_NANO_ADDR 0x09122200u
#define NANO_TO_PI_MAGIC 0x4932504eu
#define PI_TO_NANO_MAGIC 0x4e324950u
#define PROTOCOL_VERSION 3u
#define CHECKSUM_SALT 0xa5a55a5au
#define LINK_TIMEOUT_MS 3000u

#define CMD_HELLO 0u
#define CMD_PLAY_PAUSE 1u
#define CMD_PREVIOUS 2u
#define CMD_NEXT 3u
#define CMD_VOLUME_DELTA 4u
#define CMD_BT_PAGE 10u

#define FLAG_PLAYER_READY (1u << 0)
#define FLAG_PLAYING (1u << 1)
#define FLAG_PAUSED (1u << 2)
#define FLAG_BUFFERING (1u << 3)
#define FLAG_SPEAKER (1u << 4)
#define FLAG_AUTHENTICATED (1u << 5)

typedef struct {
    uint32_t magic, version, seq, command, value, nonce, checksum;
    char address[18];
    uint8_t reserved[466];
} command_page_t;

typedef struct {
    uint32_t magic, version, generation, ack, flags;
    uint32_t position_ms, duration_ms, volume_percent, error_code, nonce, checksum;
    char track[96];
    char artist[96];
    char message[64];
    uint32_t bt_flags, bt_total, bt_page, bt_count;
    char bt_message[40];
    struct { char address[18]; uint16_t flags; char name[32]; } devices[3];
} status_page_t;

static volatile command_page_t *const s_out = (volatile command_page_t *)NANO_TO_PI_ADDR;
static volatile status_page_t *const s_in = (volatile status_page_t *)PI_TO_NANO_ADDR;
static lv_obj_t *s_status, *s_track, *s_artist, *s_progress, *s_time, *s_play, *s_volume;
static lv_obj_t *s_content;
static uint32_t s_nonce, s_seq, s_last_generation, s_last_pi_ms, s_flags;
static int s_connected, s_tab, s_bt_view;
static status_page_t s_snapshot;
static char s_bt_address[18], s_bt_name[32];
static lv_obj_t *s_bt_message, *s_bt_scan, *s_bt_page_label, *s_bt_rows[3], *s_bt_actions[3];
_Static_assert(sizeof(command_page_t) == 512, "Command page size");
_Static_assert(sizeof(status_page_t) == 512, "Status page size");

static void clear_now_playing_refs(void)
{
    s_track = NULL;
    s_artist = NULL;
    s_progress = NULL;
    s_time = NULL;
    s_play = NULL;
    s_volume = NULL;
}

static void barrier(void) { __asm__ volatile("dmb" ::: "memory"); }

static char *append_u32(char *dst, uint32_t value)
{
    char reverse[11]; int count = 0;
    if (!value) { *dst++ = '0'; return dst; }
    while (value && count < 10) { reverse[count++] = (char)('0' + value % 10u); value /= 10u; }
    while (count) *dst++ = reverse[--count];
    return dst;
}

static char *append_text(char *dst, const char *src)
{
    while (*src) *dst++ = *src++;
    return dst;
}

static void copy_volatile_text(char *dst, const volatile char *src, int size)
{
    int i;
    for (i = 0; i < size - 1; i++) { dst[i] = src[i]; if (!dst[i]) break; }
    dst[i < size ? i : size - 1] = 0;
}

static uint32_t page_checksum(const uint32_t *words, int skip)
{
    uint32_t result = CHECKSUM_SALT; int i;
    for (i = 0; i < 128; i++) if (i != skip) result ^= words[i];
    return result;
}

static void publish_address(uint32_t command, int32_t value, const char *address)
{
    uint32_t words[6]; int i;
    command_page_t page = {0};
    if (s_connected && s_snapshot.ack != s_seq) return;
    s_seq++;
    words[0] = NANO_TO_PI_MAGIC; words[1] = PROTOCOL_VERSION; words[2] = s_seq;
    words[3] = command; words[4] = (uint32_t)value; words[5] = s_nonce;
    page.magic = words[0]; page.version = words[1]; page.seq = words[2];
    page.command = words[3]; page.value = words[4]; page.nonce = words[5];
    if (address) for (i = 0; i < 17 && address[i]; i++) page.address[i] = address[i];
    page.checksum = page_checksum((const uint32_t *)&page, 6);
    s_out->magic = 0; barrier();
    for (i = 1; i < 128; i++) ((volatile uint32_t *)s_out)[i] = ((uint32_t *)&page)[i];
    barrier(); s_out->magic = NANO_TO_PI_MAGIC; barrier();
}

static void publish(uint32_t command, int32_t value) { publish_address(command, value, NULL); }

static void on_command(lv_event_t *event)
{
    publish((uint32_t)(uintptr_t)lv_event_get_user_data(event), 0);
}

static void on_volume(lv_event_t *event)
{
    publish(CMD_VOLUME_DELTA, (int32_t)(intptr_t)lv_event_get_user_data(event));
}

static lv_obj_t *make_button(lv_obj_t *parent, const char *text, int x, int width,
                             lv_event_cb_t callback, intptr_t data)
{
    lv_obj_t *button = lv_button_create(parent);
    lv_obj_set_size(button, width, 54);
    lv_obj_align(button, LV_ALIGN_BOTTOM_LEFT, x, -56);
    lv_obj_set_style_bg_color(button, lv_color_hex(0x202832), 0);
    lv_obj_set_style_radius(button, 27, 0);
    lv_obj_add_event_cb(button, callback, LV_EVENT_CLICKED, (void *)data);
    lv_obj_t *label = lv_label_create(button);
    lv_label_set_text(label, text);
    lv_obj_set_style_text_color(label, lv_color_hex(0xffffff), 0);
    lv_obj_set_style_text_font(label, &lv_font_montserrat_16, 0);
    lv_obj_center(label);
    return button;
}

static void build_now_playing(void)
{
    clear_now_playing_refs();
    lv_obj_clean(s_content);
    lv_obj_t *eyebrow = lv_label_create(s_content);
    lv_label_set_text(eyebrow, "NOW PLAYING");
    lv_obj_set_style_text_color(eyebrow, lv_color_hex(0x1db954), 0);
    lv_obj_set_style_text_font(eyebrow, &lv_font_montserrat_14, 0);
    lv_obj_align(eyebrow, LV_ALIGN_TOP_LEFT, 14, 10);

    s_track = lv_label_create(s_content);
    lv_label_set_text(s_track, "Nothing playing");
    lv_label_set_long_mode(s_track, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_set_width(s_track, 212);
    lv_obj_set_style_text_color(s_track, lv_color_hex(0xffffff), 0);
    lv_obj_set_style_text_font(s_track, &lv_font_montserrat_24, 0);
    lv_obj_align(s_track, LV_ALIGN_TOP_LEFT, 14, 45);

    s_artist = lv_label_create(s_content);
    lv_label_set_text(s_artist, "Open Spotify and select this player");
    lv_label_set_long_mode(s_artist, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_set_width(s_artist, 212);
    lv_obj_set_style_text_color(s_artist, lv_color_hex(0x9aa5b1), 0);
    lv_obj_set_style_text_font(s_artist, &lv_font_montserrat_16, 0);
    lv_obj_align(s_artist, LV_ALIGN_TOP_LEFT, 14, 83);

    s_progress = lv_bar_create(s_content);
    lv_obj_set_size(s_progress, 212, 6);
    lv_obj_align(s_progress, LV_ALIGN_TOP_MID, 0, 124);
    lv_bar_set_range(s_progress, 0, 1000);
    lv_obj_set_style_bg_color(s_progress, lv_color_hex(0x303944), LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_progress, lv_color_hex(0x1db954), LV_PART_INDICATOR);

    s_time = lv_label_create(s_content);
    lv_label_set_text(s_time, "0:00 / 0:00");
    lv_obj_set_style_text_color(s_time, lv_color_hex(0x788491), 0);
    lv_obj_set_style_text_font(s_time, &lv_font_montserrat_14, 0);
    lv_obj_align(s_time, LV_ALIGN_TOP_RIGHT, -14, 138);

    make_button(s_content, "PREV", 10, 62, on_command, CMD_PREVIOUS);
    s_play = make_button(s_content, "PLAY", 84, 72, on_command, CMD_PLAY_PAUSE);
    lv_obj_set_style_bg_color(s_play, lv_color_hex(0x1db954), 0);
    make_button(s_content, "NEXT", 168, 62, on_command, CMD_NEXT);
    make_button(s_content, "-", 37, 48, on_volume, -5);
    make_button(s_content, "+", 155, 48, on_volume, 5);
    lv_obj_align(lv_obj_get_child(s_content, -2), LV_ALIGN_BOTTOM_LEFT, 37, 4);
    lv_obj_align(lv_obj_get_child(s_content, -1), LV_ALIGN_BOTTOM_LEFT, 155, 4);

    s_volume = lv_label_create(s_content);
    lv_label_set_text(s_volume, "VOL 0%");
    lv_obj_set_style_text_color(s_volume, lv_color_hex(0xcbd3db), 0);
    lv_obj_set_style_text_font(s_volume, &lv_font_montserrat_14, 0);
    lv_obj_align(s_volume, LV_ALIGN_BOTTOM_MID, 0, 20);
}

static void show_placeholder(const char *title, const char *detail)
{
    clear_now_playing_refs();
    lv_obj_clean(s_content);
    lv_obj_t *heading = lv_label_create(s_content);
    lv_label_set_text(heading, title);
    lv_obj_set_style_text_color(heading, lv_color_hex(0xffffff), 0);
    lv_obj_set_style_text_font(heading, &lv_font_montserrat_28, 0);
    lv_obj_align(heading, LV_ALIGN_TOP_LEFT, 14, 24);
    lv_obj_t *body = lv_label_create(s_content);
    lv_label_set_text(body, detail);
    lv_obj_set_width(body, 205);
    lv_label_set_long_mode(body, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_color(body, lv_color_hex(0x9aa5b1), 0);
    lv_obj_set_style_text_font(body, &lv_font_montserrat_16, 0);
    lv_obj_align(body, LV_ALIGN_TOP_LEFT, 14, 78);
}

static lv_obj_t *bt_button(const char *text, int y, int x, int width, lv_event_cb_t cb, intptr_t data)
{
    lv_obj_t *b = make_button(s_content, text, x, width, cb, data);
    lv_obj_set_pos(b, x, y); lv_obj_set_size(b, width, 44);
    lv_obj_set_style_radius(b, 10, 0);
    return b;
}

static lv_obj_t *bt_label(const char *text, int y)
{
    lv_obj_t *l = lv_label_create(s_content); lv_label_set_text(l, text);
    lv_obj_set_width(l, 216); lv_obj_set_pos(l, 12, y);
    lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_color(l, lv_color_hex(0xcbd3db), 0);
    lv_obj_set_style_text_font(l, &lv_font_montserrat_14, 0);
    return l;
}

static void build_bluetooth(void);
static void update_bluetooth(void);

static void on_bt_action(lv_event_t *e)
{
    uint32_t command = (uint32_t)(uintptr_t)lv_event_get_user_data(e);
    if (command == 14 && s_bt_view != 2) { s_bt_view = 2; build_bluetooth(); return; }
    publish_address(command, 0, command == 11 ? NULL : s_bt_address);
    if (command == 14) { s_bt_view = 0; build_bluetooth(); }
}

static void on_bt_back(lv_event_t *e)
{
    (void)e; s_bt_view = 0; build_bluetooth();
}

static void on_bt_row(lv_event_t *e)
{
    int row = (int)(intptr_t)lv_event_get_user_data(e), i;
    if (row >= (int)s_snapshot.bt_count) return;
    for (i = 0; i < 18; i++) s_bt_address[i] = s_snapshot.devices[row].address[i];
    for (i = 0; i < 32; i++) s_bt_name[i] = s_snapshot.devices[row].name[i];
    s_bt_address[17] = 0; s_bt_name[31] = 0;
    s_bt_view = 1; build_bluetooth();
}

static void on_bt_page(lv_event_t *e)
{
    int page = (int)s_snapshot.bt_page + (int)(intptr_t)lv_event_get_user_data(e);
    if (page >= 0 && (uint32_t)page * 3 < s_snapshot.bt_total) publish(CMD_BT_PAGE, page);
}

static void build_bluetooth(void)
{
    int i;
    clear_now_playing_refs(); lv_obj_clean(s_content);
    for (i = 0; i < 3; i++) { s_bt_rows[i] = NULL; s_bt_actions[i] = NULL; }
    s_bt_scan = NULL; s_bt_page_label = NULL;
    if (s_bt_view == 0) {
        bt_label("BLUETOOTH AUDIO", 6);
        s_bt_scan = bt_button("Scan for speakers", 30, 12, 216, on_bt_action, 11);
        s_bt_message = bt_label("Checking Bluetooth...", 82);
        for (i = 0; i < 3; i++) s_bt_rows[i] = bt_button("", 126 + i * 50, 12, 216, on_bt_row, i);
        bt_button("<", 283, 12, 54, on_bt_page, -1);
        bt_button(">", 283, 174, 54, on_bt_page, 1);
        s_bt_page_label = bt_label("", 295); lv_obj_set_pos(s_bt_page_label, 76, 295); lv_obj_set_width(s_bt_page_label, 90);
    } else {
        bt_label(s_bt_name, 8); bt_label(s_bt_address, 44);
        s_bt_message = bt_label("", 78);
        if (s_bt_view == 2) {
            bt_label("Forget this saved speaker? Pair again to use it later.", 136);
            s_bt_actions[0] = bt_button("Forget", 222, 12, 216, on_bt_action, 14);
        } else {
            s_bt_actions[0] = bt_button("Pair / Connect", 138, 12, 216, on_bt_action, 12);
            s_bt_actions[1] = bt_button("Disconnect", 188, 12, 216, on_bt_action, 13);
            s_bt_actions[2] = bt_button("Forget...", 238, 12, 216, on_bt_action, 14);
        }
        bt_button("Back", 288, 12, 216, on_bt_back, 0);
    }
    update_bluetooth();
}

static void bt_enabled(lv_obj_t *o, int enabled)
{
    if (!o) return;
    if (enabled) lv_obj_remove_state(o, LV_STATE_DISABLED);
    else lv_obj_add_state(o, LV_STATE_DISABLED);
}

static void update_bluetooth(void)
{
    int i, ready = s_connected && !(s_snapshot.bt_flags & 4u) && s_snapshot.ack == s_seq;
    char text[80], *p;
    if (s_tab != 2 || !s_bt_message) return;
    lv_label_set_text(s_bt_message, s_connected ? s_snapshot.bt_message : "Pi link offline");
    if (s_bt_scan) {
        lv_label_set_text(lv_obj_get_child(s_bt_scan, 0), (s_snapshot.bt_flags & 2u) ? "Stop scanning" : "Scan for speakers");
        bt_enabled(s_bt_scan, ready);
    }
    for (i = 0; i < 3; i++) {
        bt_enabled(s_bt_actions[i], ready);
        if (!s_bt_rows[i]) continue;
        if (i >= (int)s_snapshot.bt_count) {
            lv_obj_add_flag(s_bt_rows[i], LV_OBJ_FLAG_HIDDEN); continue;
        }
        lv_obj_remove_flag(s_bt_rows[i], LV_OBJ_FLAG_HIDDEN);
        p = append_text(text, s_snapshot.devices[i].name);
        p = append_text(p, (s_snapshot.devices[i].flags & 4u) ? " [audio]" :
                           (s_snapshot.devices[i].flags & 2u) ? " [linked]" :
                           (s_snapshot.devices[i].flags & 1u) ? " [saved]" : " [new]");
        *p = 0;
        lv_obj_t *label = lv_obj_get_child(s_bt_rows[i], 0);
        lv_label_set_text(label, text); lv_obj_set_width(label, 192);
        lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
        bt_enabled(s_bt_rows[i], ready);
    }
    if (s_bt_page_label) {
        p = append_u32(text, s_snapshot.bt_total ? s_snapshot.bt_page + 1 : 0);
        *p++ = '/'; p = append_u32(p, (s_snapshot.bt_total + 2) / 3); *p = 0;
        lv_label_set_text(s_bt_page_label, text);
    }
}

static void on_tab(lv_event_t *event)
{
    intptr_t tab = (intptr_t)lv_event_get_user_data(event);
    s_tab = (int)tab;
    if (tab == 0) build_now_playing();
    else if (tab == 1) show_placeholder("Library", "Playlists and saved presets will appear here in the next build.");
    else { s_bt_view = 0; build_bluetooth(); publish(CMD_BT_PAGE, 0); }
}

static void update_status(void)
{
    uint32_t words[10], generation, flags, position, duration, volume, nonce, received;
    status_page_t page; int i;
    for (i = 0; i < 128; i++) ((uint32_t *)&page)[i] = ((volatile uint32_t *)s_in)[i];
    barrier();
    if (page.magic != s_in->magic || page.generation != s_in->generation) return;
    char track[96], artist[96], message[64], time_text[32], volume_text[16];
    char *p;
    words[0] = page.magic; barrier();
    words[1] = page.version; generation = words[2] = page.generation;
    words[3] = page.ack; flags = words[4] = page.flags;
    position = words[5] = page.position_ms; duration = words[6] = page.duration_ms;
    volume = words[7] = page.volume_percent; words[8] = page.error_code;
    nonce = words[9] = page.nonce; received = page.checksum; barrier();
    if (words[0] != PI_TO_NANO_MAGIC || words[1] != PROTOCOL_VERSION || nonce != s_nonce ||
        received != page_checksum((const uint32_t *)&page, 10) || generation == s_last_generation) return;
    if (page.bt_count > 3 || page.bt_page > 10000 || page.bt_total > 30000) return;
    page.bt_message[39] = 0;
    for (i = 0; i < 3; i++) { page.devices[i].name[31] = 0; page.devices[i].address[17] = 0; }
    s_snapshot = page;
    copy_volatile_text(track, page.track, sizeof(track));
    copy_volatile_text(artist, page.artist, sizeof(artist));
    copy_volatile_text(message, page.message, sizeof(message));
    s_last_generation = generation; s_last_pi_ms = hb_time_uptime_ms(); s_connected = 1; s_flags = flags;
    lv_label_set_text(s_status, message[0] ? message : "Pi connected");
    lv_obj_set_style_text_color(s_status, lv_color_hex((flags & FLAG_PLAYER_READY) ? 0x1db954 : 0xf5a623), 0);
    update_bluetooth();
    if (!s_track) return;
    lv_label_set_text(s_track, track[0] ? track : "Nothing playing");
    lv_label_set_text(s_artist, artist[0] ? artist : "Select this player in Spotify");
    lv_label_set_text(lv_obj_get_child(s_play, 0), (flags & FLAG_PLAYING) ? "PAUSE" : "PLAY");
    lv_bar_set_value(s_progress, duration ? (int)((position * 1000ull) / duration) : 0, LV_ANIM_OFF);
    p = time_text; p = append_u32(p, position / 60000u); *p++ = ':';
    if ((position / 1000u) % 60u < 10u) *p++ = '0';
    p = append_u32(p, (position / 1000u) % 60u);
    p = append_text(p, " / "); p = append_u32(p, duration / 60000u); *p++ = ':';
    if ((duration / 1000u) % 60u < 10u) *p++ = '0';
    p = append_u32(p, (duration / 1000u) % 60u);
    *p = 0;
    lv_label_set_text(s_time, time_text);
    p = append_text(volume_text, "VOL "); p = append_u32(p, volume); *p++ = '%'; *p = 0;
    lv_label_set_text(s_volume, volume_text);
}

static void on_tick(void)
{
    uint32_t now = hb_time_uptime_ms();
    update_status();
    if (s_connected && now - s_last_pi_ms > LINK_TIMEOUT_MS) {
        s_connected = 0;
        lv_label_set_text(s_status, "Pi link offline");
        lv_obj_set_style_text_color(s_status, lv_color_hex(0xe04444), 0);
        update_bluetooth();
    }
}

HB_APP_ENTRY(payload_entry)
{
    lv_obj_t *screen = lv_screen_active();
    int i; const char *tabs[] = {"NOW", "LIBRARY", "SYSTEM"};
    s_nonce = hb_time_uptime_ms() ^ 0x73706f74u; s_seq = 0; s_last_generation = 0;
    s_last_pi_ms = 0; s_connected = 0; s_flags = 0;
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x0b0f14), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, "Spotify");
    lv_obj_set_style_text_color(title, lv_color_hex(0xffffff), 0);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_24, 0);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, 12, 14);
    s_status = lv_label_create(screen);
    lv_label_set_text(s_status, "Connecting to Pi...");
    lv_obj_set_style_text_color(s_status, lv_color_hex(0xf5a623), 0);
    lv_obj_set_style_text_font(s_status, &lv_font_montserrat_14, 0);
    lv_obj_align(s_status, LV_ALIGN_TOP_RIGHT, -10, 20);
    s_content = lv_obj_create(screen);
    lv_obj_set_size(s_content, 240, 336); lv_obj_align(s_content, LV_ALIGN_TOP_MID, 0, 52);
    lv_obj_set_style_bg_opa(s_content, LV_OPA_TRANSP, 0); lv_obj_set_style_border_width(s_content, 0, 0);
    lv_obj_set_style_pad_all(s_content, 0, 0); lv_obj_clear_flag(s_content, LV_OBJ_FLAG_SCROLLABLE);
    for (i = 0; i < 3; i++) {
        lv_obj_t *button = lv_button_create(screen);
        lv_obj_set_size(button, 80, 44); lv_obj_align(button, LV_ALIGN_BOTTOM_LEFT, i * 80, 0);
        lv_obj_set_style_radius(button, 0, 0);
        lv_obj_set_style_bg_color(button, lv_color_hex(i == 0 ? 0x1db954 : 0x161d25), 0);
        lv_obj_add_event_cb(button, on_tab, LV_EVENT_CLICKED, (void *)(intptr_t)i);
        lv_obj_t *label = lv_label_create(button); lv_label_set_text(label, tabs[i]);
        lv_obj_set_style_text_font(label, &lv_font_montserrat_14, 0); lv_obj_center(label);
    }
    build_now_playing();
    publish(CMD_HELLO, 0);
    hb_lv_set_frame_cb(on_tick);
}
