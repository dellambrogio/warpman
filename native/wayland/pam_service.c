#include "pam_service.h"

#include <stdio.h>
#include <unistd.h>

static const char *const services[] = {"swaylock-warpman", "swaylock-plugin", "swaylock"};
static const char *const dirs[] = {"/etc/pam.d", "/usr/lib/pam.d"};

static const char *find(void)
{
    char path[256];
    for (size_t i = 0; i < sizeof services / sizeof *services; i++)
        for (size_t d = 0; d < sizeof dirs / sizeof *dirs; d++) {
            snprintf(path, sizeof path, "%s/%s", dirs[d], services[i]);
            if (access(path, R_OK) == 0)
                return services[i];
        }
    return NULL;
}

const char *warpman_pam_service(void)
{
    const char *s = find();
    return s ? s : services[0];
}

int warpman_pam_service_found(void)
{
    return find() != NULL;
}
