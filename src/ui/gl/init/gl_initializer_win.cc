// Copyright 2016 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "ui/gl/init/gl_initializer.h"

#include <dwmapi.h>

#include "base/at_exit.h"
#include "base/base_paths.h"
#include "base/bind.h"
#include "base/command_line.h"
#include "base/files/file_path.h"
#include "base/lazy_instance.h"
#include "base/logging.h"
#include "base/native_library.h"
#include "base/path_service.h"
#include "base/strings/stringprintf.h"
#include "base/threading/thread_restrictions.h"
#include "base/trace_event/trace_event.h"
#include "base/win/windows_version.h"
// TODO(jmadill): Apply to all platforms eventually
#include "ui/gl/angle_platform_impl.h"
#include "ui/gl/gl_bindings.h"
#include "ui/gl/gl_egl_api_implementation.h"
#include "ui/gl/gl_features.h"
#include "ui/gl/gl_gl_api_implementation.h"
#include "ui/gl/gl_osmesa_api_implementation.h"
#include "ui/gl/gl_surface_egl.h"
#include "ui/gl/gl_surface_wgl.h"
#include "ui/gl/gl_wgl_api_implementation.h"
#include "ui/gl/vsync_provider_win.h"

#if defined(IS_BLPWTK2)
#include <blpwtk2_products.h>
#endif

namespace gl {
namespace init {

namespace {

const wchar_t kD3DCompiler[] = L"D3DCompiler_47.dll";

// TODO(jmadill): Apply to all platforms eventually
base::LazyInstance<ANGLEPlatformImpl> g_angle_platform_impl =
    LAZY_INSTANCE_INITIALIZER;

ANGLEPlatformShutdownFunc g_angle_platform_shutdown = nullptr;

bool LoadD3DXLibrary(const base::FilePath& module_path,
                     const base::FilePath::StringType& name) {
  base::NativeLibrary library =
      base::LoadNativeLibrary(base::FilePath(name), nullptr);
  if (!library) {
    library = base::LoadNativeLibrary(module_path.Append(name), nullptr);
    if (!library) {
      DVLOG(1) << name << " not found.";
      return false;
    }
  }
  return true;
}

bool InitializeStaticOSMesaInternal() {
  base::FilePath module_path;
  PathService::Get(base::DIR_MODULE, &module_path);
  base::NativeLibrary library =
      base::LoadNativeLibrary(module_path.Append(L"osmesa.dll"), nullptr);
  if (!library) {
    PathService::Get(base::DIR_EXE, &module_path);
    library =
        base::LoadNativeLibrary(module_path.Append(L"osmesa.dll"), nullptr);
    if (!library) {
      DVLOG(1) << "osmesa.dll not found";
      return false;
    }
  }

  GLGetProcAddressProc get_proc_address =
      reinterpret_cast<GLGetProcAddressProc>(
          base::GetFunctionPointerFromNativeLibrary(library,
                                                    "OSMesaGetProcAddress"));
  if (!get_proc_address) {
    DLOG(ERROR) << "OSMesaGetProcAddress not found.";
    base::UnloadNativeLibrary(library);
    return false;
  }

  SetGLGetProcAddressProc(get_proc_address);
  AddGLNativeLibrary(library);
  SetGLImplementation(kGLImplementationOSMesaGL);

  InitializeStaticGLBindingsGL();
  InitializeStaticGLBindingsOSMESA();

  return true;
}

bool InitializeStaticEGLInternal() {
  base::FilePath module_path;
  if (!PathService::Get(base::DIR_MODULE, &module_path))
    return false;

  // Attempt to load the D3DX shader compiler using the default search path
  // and if that fails, using an absolute path. This is to ensure these DLLs
  // are loaded before ANGLE is loaded in case they are not in the default
  // search path.
  LoadD3DXLibrary(module_path, kD3DCompiler);

  base::FilePath gles_path;
  const base::CommandLine* command_line =
      base::CommandLine::ForCurrentProcess();
  bool using_swift_shader =
      command_line->GetSwitchValueASCII(switches::kUseGL) ==
      kGLImplementationSwiftShaderName;
  if (using_swift_shader) {
    if (!command_line->HasSwitch(switches::kSwiftShaderPath))
      return false;
    gles_path = command_line->GetSwitchValuePath(switches::kSwiftShaderPath);
    // Preload library
    LoadLibrary(L"ddraw.dll");
  } else {
    gles_path = module_path;
  }

  // Load libglesv2.dll before libegl.dll because the latter is dependent on
  // the former and if there is another version of libglesv2.dll in the dll
  // search path, it will get loaded instead.
  base::NativeLibrary gles_library =
#if !defined(IS_BLPWTK2)
      base::LoadNativeLibrary(gles_path.Append(L"libglesv2.dll"), nullptr);
#else
      base::LoadNativeLibrary(gles_path.AppendASCII(BLPCR_GLESV2_DLL_NAME), nullptr);
#endif

  if (!gles_library) {
#if !defined(IS_BLPWTK2)
    DVLOG(1) << "libglesv2.dll not found";
#else
    DVLOG(1) << BLPCR_GLESV2_DLL_NAME << " not found";
#endif
    return false;
  }

  // When using EGL, first try eglGetProcAddress and then Windows
  // GetProcAddress on both the EGL and GLES2 DLLs.
  base::NativeLibrary egl_library =
#if !defined(IS_BLPWTK2)
      base::LoadNativeLibrary(gles_path.Append(L"libegl.dll"), nullptr);
#else
      base::LoadNativeLibrary(gles_path.AppendASCII(BLPCR_EGL_DLL_NAME), nullptr);
#endif
  if (!egl_library) {
#if !defined(IS_BLPWTK2)
    DVLOG(1) << "libegl.dll not found.";
#else
    DVLOG(1) << BLPCR_EGL_DLL_NAME << " not found.";
#endif
    base::UnloadNativeLibrary(gles_library);
    return false;
  }

#if BUILDFLAG(ENABLE_SWIFTSHADER)
  if (using_swift_shader) {
    // Register key so that SwiftShader doesn't display watermark logo.
    typedef void (__stdcall *RegisterFunc)(const char* key);
    RegisterFunc reg = reinterpret_cast<RegisterFunc>(
      base::GetFunctionPointerFromNativeLibrary(gles_library, "Register"));
    if (reg) {
      reg("SS3GCKK6B448CF63");
    }
  }
#endif

  if (!using_swift_shader) {
    // Init ANGLE platform here, before we call GetPlatformDisplay().
    // TODO(jmadill): Apply to all platforms eventually
    ANGLEPlatformInitializeFunc angle_platform_init =
        reinterpret_cast<ANGLEPlatformInitializeFunc>(
            base::GetFunctionPointerFromNativeLibrary(
                gles_library, "ANGLEPlatformInitialize"));
    if (angle_platform_init) {
      angle_platform_init(&g_angle_platform_impl.Get());

      g_angle_platform_shutdown = reinterpret_cast<ANGLEPlatformShutdownFunc>(
          base::GetFunctionPointerFromNativeLibrary(gles_library,
                                                    "ANGLEPlatformShutdown"));
    }
  }

  GLGetProcAddressProc get_proc_address =
      reinterpret_cast<GLGetProcAddressProc>(
          base::GetFunctionPointerFromNativeLibrary(egl_library,
                                                    "eglGetProcAddress"));
  if (!get_proc_address) {
    LOG(ERROR) << "eglGetProcAddress not found.";
    base::UnloadNativeLibrary(egl_library);
    base::UnloadNativeLibrary(gles_library);
    return false;
  }

  SetGLGetProcAddressProc(get_proc_address);
  AddGLNativeLibrary(egl_library);
  AddGLNativeLibrary(gles_library);
  SetGLImplementation(kGLImplementationEGLGLES2);

  InitializeStaticGLBindingsGL();
  InitializeStaticGLBindingsEGL();

  return true;
}

bool InitializeStaticWGLInternal() {
  base::NativeLibrary library =
      base::LoadNativeLibrary(base::FilePath(L"opengl32.dll"), nullptr);
  if (!library) {
    DVLOG(1) << "opengl32.dll not found";
    return false;
  }

  GLGetProcAddressProc get_proc_address =
      reinterpret_cast<GLGetProcAddressProc>(
          base::GetFunctionPointerFromNativeLibrary(library,
                                                    "wglGetProcAddress"));
  if (!get_proc_address) {
    LOG(ERROR) << "wglGetProcAddress not found.";
    base::UnloadNativeLibrary(library);
    return false;
  }

  SetGLGetProcAddressProc(get_proc_address);
  AddGLNativeLibrary(library);
  SetGLImplementation(kGLImplementationDesktopGL);

  // Initialize GL surface and get some functions needed for the context
  // creation below.
  if (!GLSurfaceWGL::InitializeOneOff()) {
    LOG(ERROR) << "GLSurfaceWGL::InitializeOneOff failed.";
    return false;
  }
  wglCreateContextProc wglCreateContextFn =
      reinterpret_cast<wglCreateContextProc>(
          GetGLProcAddress("wglCreateContext"));
  wglDeleteContextProc wglDeleteContextFn =
      reinterpret_cast<wglDeleteContextProc>(
          GetGLProcAddress("wglDeleteContext"));
  wglMakeCurrentProc wglMakeCurrentFn =
      reinterpret_cast<wglMakeCurrentProc>(GetGLProcAddress("wglMakeCurrent"));

  // Create a temporary GL context to bind to entry points. This is needed
  // because wglGetProcAddress is specified to return nullptr for all queries
  // if a context is not current in MSDN documentation, and the static
  // bindings may contain functions that need to be queried with
  // wglGetProcAddress. OpenGL wiki further warns that other error values
  // than nullptr could also be returned from wglGetProcAddress on some
  // implementations, so we need to clear the WGL bindings and reinitialize
  // them after the context creation.
  HGLRC gl_context = wglCreateContextFn(GLSurfaceWGL::GetDisplayDC());
  if (!gl_context) {
    LOG(ERROR) << "Failed to create temporary context.";
    return false;
  }
  if (!wglMakeCurrentFn(GLSurfaceWGL::GetDisplayDC(), gl_context)) {
    LOG(ERROR) << "Failed to make temporary GL context current.";
    wglDeleteContextFn(gl_context);
    return false;
  }

  InitializeStaticGLBindingsGL();
  InitializeStaticGLBindingsWGL();

  wglMakeCurrent(nullptr, nullptr);
  wglDeleteContext(gl_context);

  return true;
}

}  // namespace

bool InitializeGLOneOffPlatform() {
  VSyncProviderWin::InitializeOneOff();

  switch (GetGLImplementation()) {
    case kGLImplementationDesktopGL:
      if (!GLSurfaceWGL::InitializeOneOff()) {
        LOG(ERROR) << "GLSurfaceWGL::InitializeOneOff failed.";
        return false;
      }
      break;
    case kGLImplementationEGLGLES2:
      if (!GLSurfaceEGL::InitializeOneOff(GetDC(nullptr))) {
        LOG(ERROR) << "GLSurfaceEGL::InitializeOneOff failed.";
        return false;
      }
      break;
    case kGLImplementationOSMesaGL:
    case kGLImplementationMockGL:
      break;
    default:
      NOTREACHED();
  }
  return true;
}

bool InitializeStaticGLBindings(GLImplementation implementation) {
  // Prevent reinitialization with a different implementation. Once the gpu
  // unit tests have initialized with kGLImplementationMock, we don't want to
  // later switch to another GL implementation.
  DCHECK_EQ(kGLImplementationNone, GetGLImplementation());

  // Allow the main thread or another to initialize these bindings
  // after instituting restrictions on I/O. Going forward they will
  // likely be used in the browser process on most platforms. The
  // one-time initialization cost is small, between 2 and 5 ms.
  base::ThreadRestrictions::ScopedAllowIO allow_io;

  switch (implementation) {
    case kGLImplementationOSMesaGL:
      return InitializeStaticOSMesaInternal();
    case kGLImplementationEGLGLES2:
      return InitializeStaticEGLInternal();
    case kGLImplementationDesktopGL:
      return InitializeStaticWGLInternal();
    case kGLImplementationMockGL:
      SetGLImplementation(kGLImplementationMockGL);
      InitializeStaticGLBindingsGL();
      return true;
    default:
      NOTREACHED();
  }

  return false;
}

void InitializeDebugGLBindings() {
  InitializeDebugGLBindingsEGL();
  InitializeDebugGLBindingsGL();
  InitializeDebugGLBindingsOSMESA();
  InitializeDebugGLBindingsWGL();
}

void ClearGLBindingsPlatform() {
  // TODO(jmadill): Apply to all platforms eventually
  if (g_angle_platform_shutdown) {
    g_angle_platform_shutdown();
  }

  ClearGLBindingsEGL();
  ClearGLBindingsGL();
  ClearGLBindingsOSMESA();
  ClearGLBindingsWGL();
}

}  // namespace init
}  // namespace gl
