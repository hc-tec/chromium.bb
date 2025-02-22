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

#ifndef INCLUDED_BLPWTK2_CONTEXTMENUITEM_H
#define INCLUDED_BLPWTK2_CONTEXTMENUITEM_H

#include <blpwtk2_config.h>
#include <blpwtk2_textdirection.h>

namespace blpwtk2 {

class ContextMenuItem;
class StringRef;

namespace mojom {
class ContextMenuItem;
}

                        // =====================
                        // class ContextMenuItem
                        // =====================

// This class represents a single context menu item.
class BLPWTK2_EXPORT ContextMenuItem final
{
    // DATA
    mojom::ContextMenuItem *d_impl;

  public:
    // This *must* match mojom::ContextMenuItemType
    enum class Type {
        OPTION,
        CHECKABLE_OPTION,
        GROUP,
        SEPARATOR,
        SUBMENU
    };

    // This *must* match mojom::TextDirection
    enum class TextDirection {
        LEFT_TO_RIGHT,
        RIGHT_TO_LEFT
    };

    explicit ContextMenuItem(mojom::ContextMenuItem *impl);

    StringRef label() const;
    StringRef tooltip() const;
    Type type() const;
    unsigned action() const;
    TextDirection textDirection() const;
    bool hasDirectionalOverride() const;
    bool enabled() const;
    bool checked() const;
    int numSubMenuItems() const;
    const ContextMenuItem subMenuItem(int index) const;
};

}  // close namespace blpwtk2

#endif  // INCLUDED_BLPWTK2_CONTEXTMENUITEM_H

// vim: ts=4 et

