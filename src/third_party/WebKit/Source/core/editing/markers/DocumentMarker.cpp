/*
 * Copyright (C) 2011 Google Inc. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met:
 *
 *     * Redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer.
 *     * Redistributions in binary form must reproduce the above
 * copyright notice, this list of conditions and the following disclaimer
 * in the documentation and/or other materials provided with the
 * distribution.
 *     * Neither the name of Google Inc. nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#include "core/editing/markers/DocumentMarker.h"

#include "wtf/StdLibExtras.h"

namespace blink {

DocumentMarkerDetails::~DocumentMarkerDetails() {}

class DocumentMarkerDescription final : public DocumentMarkerDetails {
 public:
  static DocumentMarkerDescription* create(const String&);

  const String& description() const { return m_description; }
  bool isDescription() const override { return true; }

 private:
  explicit DocumentMarkerDescription(const String& description)
      : m_description(description) {}

  String m_description;
};

DocumentMarkerDescription* DocumentMarkerDescription::create(
    const String& description) {
  return new DocumentMarkerDescription(description);
}

inline DocumentMarkerDescription* toDocumentMarkerDescription(
    DocumentMarkerDetails* details) {
  if (details && details->isDescription())
    return static_cast<DocumentMarkerDescription*>(details);
  return 0;
}

class DocumentMarkerTextMatch final : public DocumentMarkerDetails {
 public:
  static DocumentMarkerTextMatch* create(bool);

  bool activeMatch() const { return m_match; }
  bool isTextMatch() const override { return true; }

 private:
  explicit DocumentMarkerTextMatch(bool match) : m_match(match) {}

  bool m_match;
};

DocumentMarkerTextMatch* DocumentMarkerTextMatch::create(bool match) {
  DEFINE_STATIC_LOCAL(DocumentMarkerTextMatch, trueInstance,
                      (new DocumentMarkerTextMatch(true)));
  DEFINE_STATIC_LOCAL(DocumentMarkerTextMatch, falseInstance,
                      (new DocumentMarkerTextMatch(false)));
  return match ? &trueInstance : &falseInstance;
}

inline DocumentMarkerTextMatch* toDocumentMarkerTextMatch(
    DocumentMarkerDetails* details) {
  if (details && details->isTextMatch())
    return static_cast<DocumentMarkerTextMatch*>(details);
  return 0;
}

class TextCompositionMarkerDetails final : public DocumentMarkerDetails {
 public:
  static TextCompositionMarkerDetails* create(Color underlineColor,
                                              bool thick,
                                              Color backgroundColor);

  bool isComposition() const override { return true; }
  Color underlineColor() const { return m_underlineColor; }
  bool thick() const { return m_thick; }
  Color backgroundColor() const { return m_backgroundColor; }

 private:
  TextCompositionMarkerDetails(Color underlineColor,
                               bool thick,
                               Color backgroundColor)
      : m_underlineColor(underlineColor),
        m_backgroundColor(backgroundColor),
        m_thick(thick) {}

  Color m_underlineColor;
  Color m_backgroundColor;
  bool m_thick;
};

TextCompositionMarkerDetails* TextCompositionMarkerDetails::create(
    Color underlineColor,
    bool thick,
    Color backgroundColor) {
  return new TextCompositionMarkerDetails(underlineColor, thick,
                                          backgroundColor);
}

inline TextCompositionMarkerDetails* toTextCompositionMarkerDetails(
    DocumentMarkerDetails* details) {
  if (details && details->isComposition())
    return static_cast<TextCompositionMarkerDetails*>(details);
  return nullptr;
}

class HighlightMarkerDetails final: public DocumentMarkerDetails {
public:
    HighlightMarkerDetails(Color foregroundColor, Color backgroundColor, bool includeNonSelectableText)
        : m_foregroundColor(foregroundColor)
        , m_backgroundColor(backgroundColor)
        , m_includeNonSelectableText(includeNonSelectableText)
    {
    }

    bool isHighlightMarker() const override { return true; }
    Color foregroundColor() const { return m_foregroundColor; }
    Color backgroundColor() const { return m_backgroundColor; }
    bool includeNonSelectableText() const { return m_includeNonSelectableText; }

private:

    Color m_foregroundColor;
    Color m_backgroundColor;
    bool  m_includeNonSelectableText;
};


inline HighlightMarkerDetails* toHighlightMarkerDetails(DocumentMarkerDetails* details)
{
    if (details && details->isHighlightMarker())
        return static_cast<HighlightMarkerDetails*>(details);
    return nullptr;
}

DocumentMarker::DocumentMarker(MarkerType type,
                               unsigned startOffset,
                               unsigned endOffset,
                               const String& description,
                               uint32_t hash)
    : m_type(type),
      m_startOffset(startOffset),
      m_endOffset(endOffset),
      m_details(description.isEmpty()
                    ? nullptr
                    : DocumentMarkerDescription::create(description)),
      m_hash(hash) {}

DocumentMarker::DocumentMarker(unsigned startOffset,
                               unsigned endOffset,
                               bool activeMatch)
    : m_type(DocumentMarker::TextMatch),
      m_startOffset(startOffset),
      m_endOffset(endOffset),
      m_details(DocumentMarkerTextMatch::create(activeMatch)),
      m_hash(0) {}

DocumentMarker::DocumentMarker(unsigned startOffset,
                               unsigned endOffset,
                               Color underlineColor,
                               bool thick,
                               Color backgroundColor)
    : m_type(DocumentMarker::Composition),
      m_startOffset(startOffset),
      m_endOffset(endOffset),
      m_details(TextCompositionMarkerDetails::create(underlineColor,
                                                     thick,
                                                     backgroundColor)),
      m_hash(0) {}

DocumentMarker::DocumentMarker(unsigned startOffset,
                               unsigned endOffset,
                               Color foregroundColor,
                               Color backgroundColor,
                               bool includeNonSelectableText)
    : m_type(DocumentMarker::Highlight)
    , m_startOffset(startOffset)
    , m_endOffset(endOffset)
    , m_details(new HighlightMarkerDetails(foregroundColor, backgroundColor, includeNonSelectableText))
    , m_hash(0)
{
}

DocumentMarker::DocumentMarker(const DocumentMarker& marker)
    : m_type(marker.type()),
      m_startOffset(marker.startOffset()),
      m_endOffset(marker.endOffset()),
      m_details(marker.details()),
      m_hash(marker.hash()) {}

void DocumentMarker::shiftOffsets(int delta) {
  m_startOffset += delta;
  m_endOffset += delta;
}

void DocumentMarker::setActiveMatch(bool active) {
  m_details = DocumentMarkerTextMatch::create(active);
}

const String& DocumentMarker::description() const {
  if (DocumentMarkerDescription* details =
          toDocumentMarkerDescription(m_details.get()))
    return details->description();
  return emptyString();
}

bool DocumentMarker::activeMatch() const {
  if (DocumentMarkerTextMatch* details =
          toDocumentMarkerTextMatch(m_details.get()))
    return details->activeMatch();
  return false;
}

Color DocumentMarker::underlineColor() const {
  if (TextCompositionMarkerDetails* details =
          toTextCompositionMarkerDetails(m_details.get()))
    return details->underlineColor();
  return Color::transparent;
}

bool DocumentMarker::thick() const {
  if (TextCompositionMarkerDetails* details =
          toTextCompositionMarkerDetails(m_details.get()))
    return details->thick();
  return false;
}

Color DocumentMarker::backgroundColor() const {
  if (TextCompositionMarkerDetails* details =
          toTextCompositionMarkerDetails(m_details.get()))
    return details->backgroundColor();

    if (HighlightMarkerDetails* details = toHighlightMarkerDetails(m_details.get())) 
        return details->backgroundColor();

  return Color::transparent;
}

Color DocumentMarker::foregroundColor() const
{
    if (HighlightMarkerDetails* details = toHighlightMarkerDetails(m_details.get()))
        return details->foregroundColor();

  return Color::transparent;
}

bool DocumentMarker::includeNonSelectableText() const 
{
    if (HighlightMarkerDetails* details = toHighlightMarkerDetails(m_details.get()))
        return details->includeNonSelectableText();

    return false;
}

DEFINE_TRACE(DocumentMarker) {
  visitor->trace(m_details);
}

}  // namespace blink
