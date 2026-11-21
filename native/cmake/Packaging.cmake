# Debian packages through CPack: one .deb per component.
#   cmake --build build --target package   ->  build/<name>_<version>_<arch>.deb
#
#   swaylock   -> swaylock-warpman   (component built when WARPMAN_WAYLAND is on)
#   i3lock     -> i3lock-warpman     (component built when WARPMAN_X11 is on)

set(WARPMAN_DOC_FILES ${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE)

execute_process(COMMAND git config user.name OUTPUT_VARIABLE GIT_NAME
                OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
execute_process(COMMAND git config user.email OUTPUT_VARIABLE GIT_EMAIL
                OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
if(NOT GIT_NAME)
    set(GIT_NAME "warpman")
endif()
if(NOT GIT_EMAIL)
    set(GIT_EMAIL "warpman@localhost")
endif()
set(WARPMAN_MAINTAINER "${GIT_NAME} <${GIT_EMAIL}>" CACHE STRING "Debian package maintainer")

# copyright file per package (Debian policy: /usr/share/doc/<pkg>/copyright)
function(warpman_doc component pkg)
    set(out ${CMAKE_CURRENT_BINARY_DIR}/doc/${pkg})
    file(MAKE_DIRECTORY ${out})
    file(READ ${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE license)
    set(text "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\nUpstream-Name: warpman\n\nFiles: *\nLicense: MIT\n")
    string(REPLACE "\n\n" "\n .\n" license_ind "${license}")
    string(REPLACE "\n" "\n " license_ind "${license_ind}")
    string(APPEND text " ${license_ind}\n")
    if(ARGN)
        string(APPEND text "${ARGN}")
    endif()
    file(WRITE ${out}/copyright "${text}")
    install(FILES ${out}/copyright DESTINATION ${CMAKE_INSTALL_DATAROOTDIR}/doc/${pkg}
            COMPONENT ${component})
endfunction()

set(CPACK_GENERATOR DEB)
set(CPACK_PACKAGE_NAME warpman)
set(CPACK_PACKAGE_VERSION ${PROJECT_VERSION})
set(CPACK_PACKAGE_CONTACT "${WARPMAN_MAINTAINER}")
set(CPACK_DEBIAN_PACKAGE_MAINTAINER "${WARPMAN_MAINTAINER}")
set(CPACK_DEB_COMPONENT_INSTALL ON)
set(CPACK_DEBIAN_FILE_NAME DEB-DEFAULT)
set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)
# libepoxy loads EGL and OpenGL at run time, so shlibdeps can't see them
set(WARPMAN_GL_DEPENDS "libegl1, libopengl0 | libgl1")
set(CPACK_DEBIAN_PACKAGE_SECTION x11)
set(CPACK_DEBIAN_PACKAGE_PRIORITY optional)
set(CPACK_DEBIAN_PACKAGE_HOMEPAGE "https://github.com/dellambrogio/warpman")
set(CPACK_STRIP_FILES ON)
set(CPACK_PACKAGE_INSTALL_DIRECTORY /usr)
set(CPACK_PACKAGING_INSTALL_PREFIX /usr)
set(CPACK_COMPONENTS_ALL)

if(WARPMAN_WAYLAND)
    list(APPEND CPACK_COMPONENTS_ALL swaylock)
    set(CPACK_DEBIAN_SWAYLOCK_PACKAGE_NAME swaylock-warpman)
    set(CPACK_DEBIAN_SWAYLOCK_PACKAGE_SECTION x11)
    set(CPACK_DEBIAN_SWAYLOCK_DESCRIPTION
"screen locker for sway with an animated warped-grid background
swaylock-plugin (https://github.com/mstoeckl/swaylock-plugin), built in,
with the warpman drift animation as its background: a thin tinted grid
bent by slowly wandering lenses, rendered with OpenGL on every monitor.
.
The animation pauses on battery power and shows CPU and battery status.
Settings come from DRIFT_* environment variables or command-line options.")
    set(CPACK_DEBIAN_SWAYLOCK_PACKAGE_DEPENDS "${WARPMAN_GL_DEPENDS}")
    set(CPACK_DEBIAN_SWAYLOCK_PACKAGE_RECOMMENDS "swayidle")
    # the PAM file is a conffile
    file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/control/swaylock/conffiles
         "${CMAKE_INSTALL_FULL_SYSCONFDIR}/pam.d/swaylock-warpman\n")
    set(CPACK_DEBIAN_SWAYLOCK_PACKAGE_CONTROL_EXTRA ${CMAKE_CURRENT_BINARY_DIR}/control/swaylock/conffiles)
    warpman_doc(swaylock swaylock-warpman
"
Files: native/third_party/swaylock-plugin/*
Copyright: 2016-2019 Drew DeVault and swaylock contributors; Manuel Stoeckl
License: MIT
 See /usr/share/doc/swaylock-warpman/LICENSE.swaylock-plugin
")
endif()

if(WARPMAN_X11)
    list(APPEND CPACK_COMPONENTS_ALL i3lock)
    set(CPACK_DEBIAN_I3LOCK_PACKAGE_NAME i3lock-warpman)
    set(CPACK_DEBIAN_I3LOCK_PACKAGE_SECTION x11)
    set(CPACK_DEBIAN_I3LOCK_PACKAGE_DEPENDS "xsecurelock, ${WARPMAN_GL_DEPENDS}")
    set(CPACK_DEBIAN_I3LOCK_PACKAGE_RECOMMENDS "xss-lock")
    set(CPACK_DEBIAN_I3LOCK_DESCRIPTION
"screen locker for i3 / X11 with an animated warped-grid background
Locks the screen with xsecurelock and runs the warpman drift animation as
its saver: a thin tinted grid bent by slowly wandering lenses, rendered with
OpenGL on every monitor. A drop-in replacement for i3lock in the i3 config.
.
The animation pauses on battery power and while the monitor is off, and shows
CPU and battery status. Settings come from DRIFT_* environment variables or
command-line options.")
    warpman_doc(i3lock i3lock-warpman)
endif()

include(CPack)
