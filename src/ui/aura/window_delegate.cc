// Copyright 2016 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "ui/aura/window_delegate.h"

namespace aura {

void WindowDelegate::OnRequestClose() {
}

bool WindowDelegate::ShouldTryFocusOnMouseDown() const {
  return true;
}

}  // namespace aura
