// Copyright (c) 2012 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef CONTENT_PUBLIC_APP_CONTENT_MAIN_RUNNER_H_
#define CONTENT_PUBLIC_APP_CONTENT_MAIN_RUNNER_H_

#include "build/build_config.h"
#include "content/common/content_export.h"

#include <stdlib.h>

namespace content {
struct ContentMainParams;

// This class is responsible for content initialization, running and shutdown.
class CONTENT_EXPORT ContentMainRunner {
 public:
  virtual ~ContentMainRunner() {}

  // Disables the hack where PeekMessage is used to suppress the
  // IDC_APPSTARTING cursor from being displayed.
  static void DisablePeekMessageHack();

  // Create a new ContentMainRunner object.
  static ContentMainRunner* Create();

  // Sets the CRT error handler functions.
  static void SetCRTErrorHandlerFunctions(_invalid_parameter_handler ivph, _purecall_handler pch);

  // Initialize all necessary content state.
  virtual int Initialize(const ContentMainParams& params) = 0;

  // Perform the default run logic.
  virtual int Run() = 0;

  // Shut down the content state.
  virtual void Shutdown() = 0;
};

}  // namespace content

#endif  // CONTENT_PUBLIC_APP_CONTENT_MAIN_RUNNER_H_
