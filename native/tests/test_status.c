/* on_battery() and the status text against fake /sys/class/power_supply trees. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "status.h"

static char root[200];
static int failures;

static void put(const char *dev, const char *attr, const char *value)
{
    char path[1024];
    snprintf(path, sizeof path, "%.200s/%.100s", root, dev);
    mkdir(path, 0700);
    snprintf(path, sizeof path, "%.200s/%.100s/%.100s", root, dev, attr);
    FILE *f = fopen(path, "w");
    fprintf(f, "%s\n", value);
    fclose(f);
}

static void reset(void)
{
    char cmd[512];
    snprintf(cmd, sizeof cmd, "rm -rf '%s' && mkdir -p '%s'", root, root);
    if (system(cmd) != 0)
        exit(1);
}

static void expect(const char *what, int got, int want)
{
    printf("%-40s %s\n", what, got == want ? "ok" : "FAILED");
    failures += got != want;
}

int main(void)
{
    snprintf(root, sizeof root, "/tmp/warpman-test-status-%d", (int)getpid());

    reset();
    expect("no supplies: not on battery", wm_on_battery(root), 0);

    put("AC", "type", "Mains");
    put("AC", "online", "1");
    expect("mains only", wm_on_battery(root), 0);

    put("BAT0", "type", "Battery");
    put("BAT0", "status", "Charging");
    put("BAT0", "capacity", "80");
    expect("battery + mains online", wm_on_battery(root), 0);

    put("AC", "online", "0");
    put("BAT0", "status", "Discharging");
    expect("battery + mains offline", wm_on_battery(root), 1);

    put("ucsi-source-psy-1", "type", "USB");
    put("ucsi-source-psy-1", "online", "1");
    expect("battery + USB-C power online", wm_on_battery(root), 0);

    reset();
    put("BAT0", "type", "Battery");
    put("BAT0", "status", "Discharging");
    expect("battery only, discharging", wm_on_battery(root), 1);
    put("BAT0", "status", "Not charging");
    expect("battery only, not charging (charge limit)", wm_on_battery(root), 0);

    /* status text */
    put("BAT0", "capacity", "57");
    put("BAT0", "status", "Discharging");
    put("BAT1", "type", "Battery");
    put("BAT1", "capacity", "100");
    put("BAT1", "status", "Full");
    WmStatus st;
    wm_status_init(&st);
    st.sysfs_root = root;
    st.proc_stat = "/nonexistent";
    st.have_prev = 0;
    st.next = 0;
    wm_status_update(&st);
    printf("status text: '%s'\n", st.text);
    expect("battery text", strcmp(st.text, "BAT 57% -  BAT 100%"), 0);

    st.proc_stat = "/proc/stat";
    st.next = 0;
    wm_status_update(&st);              /* first CPU sample */
    usleep(50000);
    st.next = 0;
    wm_status_update(&st);
    printf("status text: '%s'\n", st.text);
    expect("CPU text", strncmp(st.text, "CPU ", 4), 0);
    expect("CPU then battery", strstr(st.text, "%   BAT 57% -  BAT 100%") != NULL, 1);

    char cmd[512];
    snprintf(cmd, sizeof cmd, "rm -rf '%s'", root);
    if (system(cmd) != 0)
        return 1;
    return failures ? 1 : 0;
}
