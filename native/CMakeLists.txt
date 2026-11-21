cmake_minimum_required(VERSION 3.25)
cmake_policy(SET CMP0177 NEW)
# (no DESCRIPTION: CPack would prepend it to each package's own description)
project(warpman VERSION 0.1.0 LANGUAGES C)

# packages install into /usr (and so /etc for the PAM file); override as usual
if(CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT)
    set(CMAKE_INSTALL_PREFIX /usr CACHE PATH "Install prefix" FORCE)
endif()
include(GNUInstallDirs)

option(WARPMAN_WAYLAND "Build swaylock-warpman (sway / wlroots)" ON)
option(WARPMAN_X11 "Build i3lock-warpman (X11 / xsecurelock)" ON)
option(WARPMAN_TESTS "Build the unit tests" ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "" FORCE)
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

find_package(PkgConfig REQUIRED)
pkg_check_modules(EGL REQUIRED IMPORTED_TARGET egl)
pkg_check_modules(EPOXY REQUIRED IMPORTED_TARGET epoxy)
pkg_check_modules(CAIRO REQUIRED IMPORTED_TARGET cairo)

# Our own code: strict warnings, and no FMA contraction or fast-math, so the drift
# arithmetic matches CPython's bit for bit (see core/pyrandom.h, tests/test_parity.c).
set(WARPMAN_C_FLAGS -Wall -Wextra -Wno-unused-parameter -Wshadow -ffp-contract=off
    -D_GNU_SOURCE)

# warpman_man(<name> <component>): install man/<name>.1 gzipped, as Debian expects
function(warpman_man name component)
    set(gz ${CMAKE_CURRENT_BINARY_DIR}/${name}.1.gz)
    add_custom_command(OUTPUT ${gz}
        COMMAND gzip -9nc ${PROJECT_SOURCE_DIR}/man/${name}.1 > ${gz}
        DEPENDS ${PROJECT_SOURCE_DIR}/man/${name}.1 VERBATIM)
    add_custom_target(${name}-man ALL DEPENDS ${gz})
    install(FILES ${gz} DESTINATION ${CMAKE_INSTALL_MANDIR}/man1 COMPONENT ${component})
endfunction()

add_subdirectory(core)

if(WARPMAN_WAYLAND)
    pkg_check_modules(WAYLAND_CLIENT REQUIRED IMPORTED_TARGET wayland-client>=1.20)
    pkg_check_modules(WAYLAND_EGL REQUIRED IMPORTED_TARGET wayland-egl)
    include(cmake/SwaylockPlugin.cmake)
    add_subdirectory(wayland)
endif()

if(WARPMAN_X11)
    pkg_check_modules(X11 REQUIRED IMPORTED_TARGET x11 xext)
    add_subdirectory(x11)
endif()

if(WARPMAN_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()

include(cmake/Packaging.cmake)
