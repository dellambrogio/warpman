#pragma once

/* The PAM service swaylock-plugin authenticates with: "swaylock-warpman" (installed by
 * the package), else "swaylock-plugin" or "swaylock" if one of those is installed (for
 * running from the build tree). */
const char *warpman_pam_service(void);
/* 1 if one of those services exists in /etc/pam.d or /usr/lib/pam.d */
int warpman_pam_service_found(void);
