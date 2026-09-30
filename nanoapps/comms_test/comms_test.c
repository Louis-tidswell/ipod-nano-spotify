/*
 * Pi Link Test
 *
 * Proves bidirectional USB communication over the custom NanoApps SCSI memory
 * channel. The Nano writes button events to one 512-byte page. A Pi process
 * reads those events and writes acknowledgements to a second page.
 */

#include "hb_sdk.h"
#include "lvgl/lvgl.h"

#define NANO_TO_PI_ADDR 0x09122000u
#define PI_TO_NANO_ADDR 0x09122200u
#define NANO_TO_PI_MAGIC 0x4932504eu /* bytes "NP2I" */
#define PI_TO_NANO_MAGIC 0x4e324950u /* bytes "PI2N" */
#define PROTOCOL_VERSION 1u
#define EVENT_HELLO 0u
#define EVENT_BUTTON 1u
#define STATUS_ONLINE 1u
#define CHECKSUM_SALT 0xa5a55a5au
#define LINK_TIMEOUT_MS 3000u

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t seq;
    uint32_t event;
    uint32_t value;
    uint32_t nonce;
    uint32_t checksum;
    uint8_t reserved[512 - 7 * sizeof(uint32_t)];
} mailbox_page_t;

static volatile mailbox_page_t *const s_out =
    (volatile mailbox_page_t *)NANO_TO_PI_ADDR;
static volatile mailbox_page_t *const s_in =
    (volatile mailbox_page_t *)PI_TO_NANO_ADDR;

static lv_obj_t *s_status;
static lv_obj_t *s_counter;
static lv_obj_t *s_detail;
static uint32_t s_nonce;
static uint32_t s_seq;
static uint32_t s_last_generation;
static uint32_t s_last_ack;
static uint32_t s_last_pi_ms;
static int s_connected;

static uint32_t checksum6(uint32_t a, uint32_t b, uint32_t c,
                          uint32_t d, uint32_t e, uint32_t f)
{
    return a ^ b ^ c ^ d ^ e ^ f ^ CHECKSUM_SALT;
}

static void barrier(void)
{
    __asm__ volatile("dmb" ::: "memory");
}

static char *append_u32(char *dst, uint32_t value)
{
    char reversed[11];
    int n = 0;
    if (value == 0) {
        *dst++ = '0';
        return dst;
    }
    while (value && n < 10) {
        reversed[n++] = (char)('0' + value % 10u);
        value /= 10u;
    }
    while (n) *dst++ = reversed[--n];
    return dst;
}

static char *append_text(char *dst, const char *src)
{
    while (*src) *dst++ = *src++;
    return dst;
}

static void set_detail(uint32_t sent, uint32_t ack)
{
    char text[48];
    char *p = text;
    p = append_text(p, "Sent ");
    p = append_u32(p, sent);
    p = append_text(p, "  /  Ack ");
    p = append_u32(p, ack);
    *p = 0;
    lv_label_set_text(s_detail, text);
}

static void set_counter(uint32_t value)
{
    char text[16];
    char *p = append_u32(text, value);
    *p = 0;
    lv_label_set_text(s_counter, text);
}

static void publish(uint32_t event)
{
    uint32_t value = s_seq;
    uint32_t sum = checksum6(NANO_TO_PI_MAGIC, PROTOCOL_VERSION, s_seq,
                             event, value, s_nonce);

    /* Magic is the commit marker. Clearing it first makes a concurrent host
       read fail validation instead of accepting a partly-updated request. */
    s_out->magic = 0;
    barrier();
    s_out->version = PROTOCOL_VERSION;
    s_out->seq = s_seq;
    s_out->event = event;
    s_out->value = value;
    s_out->nonce = s_nonce;
    s_out->checksum = sum;
    barrier();
    s_out->magic = NANO_TO_PI_MAGIC;
    barrier();
}

static void on_send(lv_event_t *event)
{
    (void)event;
    s_seq++;
    publish(EVENT_BUTTON);
    set_detail(s_seq, s_last_ack);
    lv_label_set_text(s_status, "Sent - waiting for Pi...");
    lv_obj_set_style_text_color(s_status, lv_color_hex(0xf5a623), 0);
}

static void poll_response(void)
{
    uint32_t magic = s_in->magic;
    barrier();
    uint32_t version = s_in->version;
    uint32_t generation = s_in->seq;
    uint32_t ack = s_in->event;
    uint32_t counter = s_in->value;
    uint32_t nonce = s_in->nonce;
    uint32_t checksum = s_in->checksum;
    barrier();

    if (magic != PI_TO_NANO_MAGIC || version != PROTOCOL_VERSION ||
        nonce != s_nonce || checksum != checksum6(magic, version, generation,
                                                   ack, counter, nonce)) {
        return;
    }

    if (generation != s_last_generation) {
        s_last_generation = generation;
        s_last_ack = ack;
        s_last_pi_ms = hb_time_uptime_ms();
        s_connected = 1;
        set_counter(counter);
        set_detail(s_seq, ack);
        lv_label_set_text(s_status,
                          ack == s_seq && s_seq != 0 ? "Round trip OK" : "Pi connected");
        lv_obj_set_style_text_color(s_status, lv_color_hex(0x1db954), 0);
    }
}

static void on_tick(void)
{
    uint32_t now = hb_time_uptime_ms();
    poll_response();
    if (s_connected && now - s_last_pi_ms > LINK_TIMEOUT_MS) {
        s_connected = 0;
        lv_label_set_text(s_status, "Pi link timed out");
        lv_obj_set_style_text_color(s_status, lv_color_hex(0xe04444), 0);
    }
}

HB_APP_ENTRY(payload_entry)
{
    hb_trace_init();
    hb_trace_log("COMMBOOT", NANO_TO_PI_ADDR, PI_TO_NANO_ADDR);

    s_nonce = hb_time_uptime_ms() ^ 0x6e616e6fu;
    s_seq = 0;
    s_last_generation = 0;
    s_last_ack = 0;
    s_last_pi_ms = 0;
    s_connected = 0;

    lv_obj_t *screen = lv_screen_active();
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x0b0f14), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);

    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, "Pi Link Test");
    lv_obj_set_style_text_color(title, lv_color_hex(0xf4f7fa), 0);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_28, 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 44);

    s_status = lv_label_create(screen);
    lv_label_set_text(s_status, "Waiting for Pi...");
    lv_obj_set_style_text_color(s_status, lv_color_hex(0xf5a623), 0);
    lv_obj_set_style_text_font(s_status, &lv_font_montserrat_16, 0);
    lv_obj_align(s_status, LV_ALIGN_TOP_MID, 0, 96);

    lv_obj_t *caption = lv_label_create(screen);
    lv_label_set_text(caption, "Pi counter");
    lv_obj_set_style_text_color(caption, lv_color_hex(0x8f9aaa), 0);
    lv_obj_set_style_text_font(caption, &lv_font_montserrat_16, 0);
    lv_obj_align(caption, LV_ALIGN_TOP_MID, 0, 145);

    s_counter = lv_label_create(screen);
    lv_label_set_text(s_counter, "0");
    lv_obj_set_style_text_color(s_counter, lv_color_hex(0xffffff), 0);
    lv_obj_set_style_text_font(s_counter, &lv_font_montserrat_48, 0);
    lv_obj_align(s_counter, LV_ALIGN_TOP_MID, 0, 174);

    lv_obj_t *button = lv_button_create(screen);
    lv_obj_set_size(button, 190, 68);
    lv_obj_align(button, LV_ALIGN_TOP_MID, 0, 258);
    lv_obj_set_style_bg_color(button, lv_color_hex(0x1db954), 0);
    lv_obj_set_style_radius(button, 18, 0);
    lv_obj_add_event_cb(button, on_send, LV_EVENT_CLICKED, NULL);

    lv_obj_t *button_label = lv_label_create(button);
    lv_label_set_text(button_label, "SEND TEST");
    lv_obj_set_style_text_color(button_label, lv_color_hex(0xffffff), 0);
    lv_obj_set_style_text_font(button_label, &lv_font_montserrat_20, 0);
    lv_obj_center(button_label);

    s_detail = lv_label_create(screen);
    lv_label_set_text(s_detail, "Sent 0  /  Ack 0");
    lv_obj_set_style_text_color(s_detail, lv_color_hex(0x8f9aaa), 0);
    lv_obj_set_style_text_font(s_detail, &lv_font_montserrat_14, 0);
    lv_obj_align(s_detail, LV_ALIGN_BOTTOM_MID, 0, -38);

    publish(EVENT_HELLO);
    hb_lv_set_frame_cb(on_tick);
}
