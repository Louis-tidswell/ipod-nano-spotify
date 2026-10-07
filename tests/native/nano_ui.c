#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include "../../nanoapps/spotify_remote/spotify_remote.c"
static uint32_t ticks = 1000;
uint32_t hb_time_uptime_ms(void) { return ticks; }
void hb_lv_set_frame_cb(void (*cb)(void)) { (void)cb; }
static uint32_t tick_cb(void) { return ticks; }
static uint32_t pixels[240 * 432];
static void flush(lv_display_t *d, const lv_area_t *a, uint8_t *p) { (void)a; (void)p; lv_display_flush_ready(d); }
static void screenshot(const char *dir, const char *name) {
    char path[512]; snprintf(path, sizeof(path), "%s/%s.ppm", dir, name);
    lv_obj_update_layout(lv_screen_active()); lv_refr_now(NULL);
    FILE *f = fopen(path, "wb"); assert(f); fprintf(f, "P6\n240 432\n255\n");
    for (int i = 0; i < 240 * 432; i++) { uint8_t rgb[] = {pixels[i] >> 16, pixels[i] >> 8, pixels[i]}; fwrite(rgb, 1, 3, f); }
    fclose(f);
}
static void in_bounds(lv_obj_t *obj) {
    if (!obj || lv_obj_has_flag(obj, LV_OBJ_FLAG_HIDDEN)) return;
    lv_obj_update_layout(lv_screen_active()); lv_area_t a; lv_obj_get_coords(obj, &a);
    if (a.x1 < 0 || a.x2 >= 240 || a.y1 < 56 || a.y2 >= 388) {
        fprintf(stderr, "Control outside content: %d,%d-%d,%d\n", a.x1,a.y1,a.x2,a.y2); abort();
    }
}
static void all_content(lv_obj_t *obj) {
    for (uint32_t i = 0; i < lv_obj_get_child_count(obj); i++) {
        lv_obj_t *child = lv_obj_get_child(obj,i);
        if (lv_obj_has_flag(child, LV_OBJ_FLAG_HIDDEN)) continue;
        in_bounds(child); all_content(child);
    }
}
static void drain(void) {
    do { s_snapshot.ack = s_seq; send_next(); } while (s_queue_count);
    s_snapshot.ack = s_seq;
}
static void fixture(const char *path) {
    FILE *f = fopen(path,"rb"); assert(f); uint8_t bytes[512]; assert(fread(bytes,1,512,f)==512); fclose(f);
    status_page_t *p = (status_page_t *)bytes;
    p->nonce = s_nonce; p->generation = s_last_generation + 1; p->ack = s_seq;
    p->checksum = page_checksum((const uint32_t *)p,10);
    memcpy((void *)s_in,bytes,512); update_status();
}
int main(int argc, char **argv) {
    assert(argc == 2);
    assert(mmap((void *)0x09122000,4096,PROT_READ|PROT_WRITE,MAP_ANONYMOUS|MAP_PRIVATE|MAP_FIXED,-1,0) != MAP_FAILED);
    lv_init(); lv_tick_set_cb(tick_cb); lv_display_t *d = lv_display_create(240,432);
    lv_display_set_color_format(d,LV_COLOR_FORMAT_XRGB8888); lv_display_set_buffers(d,pixels,NULL,sizeof(pixels),LV_DISPLAY_RENDER_MODE_DIRECT); lv_display_set_flush_cb(d,flush);
    payload_entry(); s_connected=1; drain();
    char path[512]; snprintf(path,sizeof(path),"%s/bt.bin",argv[1]); fixture(path);
    lv_obj_send_event(s_tabs[2], LV_EVENT_CLICKED, NULL); drain(); update_view();
    assert(s_tab==2 && s_bt.count==3); all_content(s_content);
    snprintf(path,sizeof(path),"%s/config.bin",argv[1]); fixture(path);
    assert(s_config_label); assert(strcmp(lv_label_get_text(s_config_label),"http://192.168.1.120:8080")==0);
    all_content(s_content); screenshot(argv[1],"system");
    for (uint32_t i=0;i<lv_obj_get_child_count(s_content);i++) {
        lv_obj_t *o=lv_obj_get_child(s_content,i);
        if (lv_obj_check_type(o,&lv_button_class)) { lv_obj_send_event(o,LV_EVENT_CLICKED,NULL); break; }
    }
    assert(s_bt_view==0); all_content(s_content);
    assert(lv_obj_has_flag(lv_obj_get_parent(s_rows[0]),LV_OBJ_FLAG_SCROLLABLE));
    screenshot(argv[1],"bluetooth");
    lv_obj_send_event(s_rows[0],LV_EVENT_CLICKED,NULL); all_content(s_content);
    assert(strcmp(s_bt_address,"C0:28:8D:72:38:C2")==0); screenshot(argv[1],"bluetooth-device");
    lv_obj_send_event(s_actions[2],LV_EVENT_CLICKED,NULL); all_content(s_content); screenshot(argv[1],"forget");
    lv_obj_send_event(s_tabs[1],LV_EVENT_CLICKED,NULL); drain();
    snprintf(path,sizeof(path),"%s/library.bin",argv[1]); fixture(path);
    assert(s_library.count==2); all_content(s_content); screenshot(argv[1],"library");
    lv_obj_send_event(s_rows[1],LV_EVENT_CLICKED,NULL); drain();
    assert(s_out->command==21); assert(strcmp((const char *)s_out->payload,"spotify:playlist:0123456789ABCDEFGHIJKL")==0);
    lv_obj_send_event(s_tabs[0],LV_EVENT_CLICKED,NULL); drain(); all_content(s_content);
    for (uint32_t i=0;i<ART_CHUNKS;i++) { snprintf(path,sizeof(path),"%s/art-%u.bin",argv[1],i); fixture(path); }
    assert(s_art_ready); assert(s_art_image.data_size==ART_BYTES); assert(s_art_id==1234);
    assert(!lv_obj_has_flag(s_cover,LV_OBJ_FLAG_HIDDEN)); all_content(s_content); screenshot(argv[1],"now"); drain();
    /* Playback and volume still emit the existing commands after navigating all views. */
    lv_obj_send_event(s_play,LV_EVENT_CLICKED,NULL); drain(); assert(s_out->command==1);
    for (uint32_t i=0;i<lv_obj_get_child_count(s_content);i++) {
        lv_obj_t *o=lv_obj_get_child(s_content,i);
        if (lv_obj_check_type(o,&lv_button_class)) {
            lv_obj_t *l=lv_obj_get_child(o,0);
            if (strcmp(lv_label_get_text(l),"+")==0) { lv_obj_send_event(o,LV_EVENT_CLICKED,NULL); drain(); assert(s_out->command==4 && s_out->value==5); }
        }
    }
    /* Reject corruption without changing the displayed track. */
    ((volatile uint8_t *)s_in)[45] ^= 1; uint32_t generation=s_last_generation; update_status(); assert(s_last_generation==generation);
    for(int i=0;i<30;i++) { lv_obj_send_event(s_tabs[i%3],LV_EVENT_CLICKED,NULL); drain(); update_view(); all_content(s_content); }
    puts("Nano UI: bounds, scrolling, navigation, playlist identity, art assembly and existing commands passed");
    return 0;
}
