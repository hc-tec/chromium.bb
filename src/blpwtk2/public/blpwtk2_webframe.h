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

#ifndef INCLUDED_BLPWTK2_WEBFRAME_H
#define INCLUDED_BLPWTK2_WEBFRAME_H

#include <blpwtk2_config.h>
#include <v8.h>

namespace blpwtk2 {

class WebContentSettingsDelegate;

class WebFrame {
public:
    virtual v8::Local<v8::Context> mainWorldScriptContext() const = 0;
    virtual v8::Isolate* scriptIsolate() const = 0;
    virtual void setContentSettingsDelegate(WebContentSettingsDelegate *contentSettingsDelegate) = 0;

protected:
    // Destroy this WebView.  Note that clients of blpwtk2 should not delete
    // this object.  It will be deleted automatically when the WebView gets
    // destroyed.
    virtual ~WebFrame();
};

}  // close namespace blpwtk2

#endif  // INCLUDED_BLPWTK2_WEBFRAME_H

// vim: ts=4 et

