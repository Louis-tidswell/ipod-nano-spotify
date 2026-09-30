/* Basic full-shell Spotify controller for iPod nano 7. */
#include "hb_sdk.h"
#include "lvgl/lvgl.h"

#define NANO_TO_PI_ADDR 0x09122000u
#define PI_TO_NANO_ADDR 0x09122200u
#define NANO_TO_PI_MAGIC 0x4932504eu
#define PI_TO_NANO_MAGIC 0x4e324950u
#define PROTOCOL_VERSION 2u
#define CHECKSUM_SALT 0xa5a55a5au
#define LINK_TIMEOUT_MS 3000u

#define CMD_HELLO 0u
#define CMD_PLAY_PAUSE 1u
#define CMD_PREVIOUS 2u
#define CMD_NEXT 3u
#define CMD_VOLUME_DELTA 4u

#define FLAG_PLAYER_READY (1u << 0)
#define FLAG_PLAYING (1u << 1)
#define FLAG_PAUSED (1u << 2)
#define FLAG_BUFFERING (1u << 3)
#define FLAG_SPEAKER (1u << 4)
#define FLAG_AUTHENTICATED (1u << 5)

typedef struct {
    uint32_t magic, version, seq, command, value, nonce, checksum;
    uint8_t reserved[512 - 7 * sizeof(uint32_t)];
} command_page_t;

typedef struct {
    uint32_t magic, version, generation, ack, flags;
    uint32_t position_ms, duration_ms, volume_percent, error_code, nonce, checksum;
    char track[96];
    char artist[96];
    char message[64];
    uint8_t reserved[512 - 11 * sizeof(uint32_t) - 256];
} status_page_t;

static volatile command_page_t *const s_out = (volatile command_page_t *)NANO_TO_PI_ADDR;
static volatile status_page_t *const s_in = (volatile status_page_t *)PI_TO_NANO_ADDR;
static lv_obj_t *s_status, *s_track, *s_artist, *s_progress, *s_time, *s_play, *s_volume;
static lv_obj_t *s_content;
static uint32_t s_nonce, s_seq, s_last_generation, s_last_pi_ms, s_flags;
static int s_connected;

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

static uint32_t checksum_words(const uint32_t *words, int count)
{
    uint32_t sum = CHECKSUM_SALT;
    int i;
    for (i = 0; i < count; i++) sum ^= words[i];
    return sum;
}

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

static void publish(uint32_t command, int32_t value)
{
    uint32_t words[6];
    s_seq++;
    words[0] = NANO_TO_PI_MAGIC; words[1] = PROTOCOL_VERSION; words[2] = s_seq;
    words[3] = command; words[4] = (uint32_t)value; words[5] = s_nonce;
    s_out->magic = 0; barrier();
    s_out->version = words[1]; s_out->seq = words[2]; s_out->command = words[3];
    s_out->value = words[4]; s_out->nonce = words[5];
    s_out->checksum = checksum_words(words, 6); barrier();
    s_out->magic = NANO_TO_PI_MAGIC; barrier();
}

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

static void on_tab(lv_event_t *event)
{
    intptr_t tab = (intptr_t)lv_event_get_user_data(event);
    if (tab == 0) build_now_playing();
    else if (tab == 1) show_placeholder("Library", "Playlists and saved presets will appear here in the next build.");
    else show_placeholder("System", "Bluetooth speaker, network and player diagnostics will appear here.");
}

static void update_status(void)
{
    uint32_t words[10], generation, flags, position, duration, volume, nonce, received;
    char track[96], artist[96], message[64], time_text[32], volume_text[16];
    char *p;
    words[0] = s_in->magic; barrier();
    words[1] = s_in->version; generation = words[2] = s_in->generation;
    words[3] = s_in->ack; flags = words[4] = s_in->flags;
    position = words[5] = s_in->position_ms; duration = words[6] = s_in->duration_ms;
    volume = words[7] = s_in->volume_percent; words[8] = s_in->error_code;
    nonce = words[9] = s_in->nonce; received = s_in->checksum; barrier();
    if (words[0] != PI_TO_NANO_MAGIC || words[1] != PROTOCOL_VERSION || nonce != s_nonce ||
        received != checksum_words(words, 10) || generation == s_last_generation) return;
    copy_volatile_text(track, s_in->track, sizeof(track));
    copy_volatile_text(artist, s_in->artist, sizeof(artist));
    copy_volatile_text(message, s_in->message, sizeof(message));
    s_last_generation = generation; s_last_pi_ms = hb_time_uptime_ms(); s_connected = 1; s_flags = flags;
    lv_label_set_text(s_status, message[0] ? message : "Pi connected");
    lv_obj_set_style_text_color(s_status, lv_color_hex((flags & FLAG_PLAYER_READY) ? 0x1db954 : 0xf5a623), 0);
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
