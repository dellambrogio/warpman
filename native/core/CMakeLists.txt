add_library(warpman_core STATIC
    pyrandom.c scene.c drift.c status.c opts.c egl.c gl_render.c)
target_include_directories(warpman_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_options(warpman_core PRIVATE ${WARPMAN_C_FLAGS})
target_link_libraries(warpman_core PUBLIC PkgConfig::EGL PkgConfig::EPOXY PkgConfig::CAIRO m)
