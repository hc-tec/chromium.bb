/*
 * Copyright (C) 2013 Bloomberg Finance L.P.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to
 * deal in the Software without restriction, including without limitation the
 * rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
 * sell copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS," WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
 * FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
 * IN THE SOFTWARE.
 */

#include <locale.h>
#include <windows.h>  // NOLINT

#include <blpwtk2_config.h>
#include <blpwtk2_toolkitimpl.h>

#include <base/logging.h>  // for DCHECK

HANDLE g_instDLL;

extern "C" {
BOOL WINAPI DllMain(HANDLE hinstDLL,
                    DWORD dwReason,
                    LPVOID lpvReserved) {
    g_instDLL = hinstDLL;

    if (DLL_PROCESS_ATTACH == dwReason) {
        setlocale(LC_ALL, NULL);
    }

    if (DLL_PROCESS_DETACH == dwReason) {
        DCHECK(!blpwtk2::ToolkitImpl::instance())
            << "Make sure you call blpwtk2::Toolkit::destroy()";
    }

    return TRUE;
}
}


// vim: ts=4 et

