// Copyright 2015 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "content/common/in_process_child_thread_params.h"

namespace content {

InProcessChildThreadParams::InProcessChildThreadParams(
    scoped_refptr<base::SequencedTaskRunner> io_runner,
    const std::string& service_request_token,
    int mojo_controller_handle)
    : io_runner_(io_runner),
      service_request_token_(service_request_token),
      mojo_controller_handle_(mojo_controller_handle) {}

InProcessChildThreadParams::InProcessChildThreadParams(
    const InProcessChildThreadParams& other) = default;

InProcessChildThreadParams::~InProcessChildThreadParams() {
}

}  // namespace content
