#include <stdint.h>
#include <stddef.h>
#define HB_APP_ENTRY(name) void name(void)
uint32_t hb_time_uptime_ms(void);
void hb_lv_set_frame_cb(void (*cb)(void));
