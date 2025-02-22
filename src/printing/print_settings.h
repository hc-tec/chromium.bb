// Copyright (c) 2012 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef PRINTING_PRINT_SETTINGS_H_
#define PRINTING_PRINT_SETTINGS_H_

#include <string>

#include "base/strings/string16.h"
#include "printing/page_range.h"
#include "printing/page_setup.h"
#include "printing/print_job_constants.h"
#include "printing/printing_export.h"
#include "ui/gfx/geometry/rect.h"

namespace printing {

// Returns true if |color_mode| is color and not B&W.
PRINTING_EXPORT bool IsColorModelSelected(int color_mode);

#if defined(USE_CUPS)
// Get the color model setting name and value for the |color_mode|.
PRINTING_EXPORT void GetColorModelForMode(int color_mode,
                                          std::string* color_setting_name,
                                          std::string* color_value);
#endif

// Inform the printing system that it may embed this user-agent string
// in its output's metadata.
PRINTING_EXPORT void SetAgent(const std::string& user_agent);
PRINTING_EXPORT const std::string& GetAgent();

// OS-independent print settings.
class PRINTING_EXPORT PrintSettings {
 public:
  // Media properties requested by the user. Default instance represents
  // default media selection.
  struct RequestedMedia {
    // Size of the media, in microns.
    gfx::Size size_microns;
    // Platform specific id to map it back to the particular media.
    std::string vendor_id;

    bool IsDefault() const {
      return size_microns.IsEmpty() && vendor_id.empty();
    }
  };

  PrintSettings();
  PrintSettings(const PrintSettings& other);
  ~PrintSettings();

  // Reinitialize the settings to the default values.
  void Clear();

  void SetCustomMargins(const PageMargins& requested_margins_in_points);
  const PageMargins& requested_custom_margins_in_points() const {
    return requested_custom_margins_in_points_;
  }
  void set_margin_type(MarginType margin_type) { margin_type_ = margin_type; }
  MarginType margin_type() const { return margin_type_; }

  // Updates the orientation and flip the page if needed.
  void SetOrientation(bool landscape);
  bool landscape() const { return landscape_; }

  // Updates user requested media.
  void set_requested_media(const RequestedMedia& media) {
    requested_media_ = media;
  }
  // Media properties requested by the user. Translated into device media by the
  // platform specific layers.
  const RequestedMedia& requested_media() const {
    return requested_media_;
  }

  // Set printer printable area in in device units.
  // Some platforms already provide flipped area. Set |landscape_needs_flip|
  // to false on those platforms to avoid double flipping.
  void SetPrinterPrintableArea(const gfx::Size& physical_size_device_units,
                               const gfx::Rect& printable_area_device_units,
                               bool landscape_needs_flip);
  const PageSetup& page_setup_device_units() const {
    return page_setup_device_units_;
  }

  void set_device_name(const base::string16& device_name) {
    device_name_ = device_name;
  }
  const base::string16& device_name() const { return device_name_; }

  void set_dpi(int dpi) { dpi_ = dpi; }
  int dpi() const { return dpi_; }

  void set_scale_factor(double scale_factor) { scale_factor_ = scale_factor; }
  double scale_factor() const { return scale_factor_; }

  void set_supports_alpha_blend(bool supports_alpha_blend) {
    supports_alpha_blend_ = supports_alpha_blend;
  }
  bool supports_alpha_blend() const { return supports_alpha_blend_; }

  int device_units_per_inch() const {
#if defined(OS_MACOSX)
    return 72;
#else  // defined(OS_MACOSX)
    return dpi();
#endif  // defined(OS_MACOSX)
  }

  void set_ranges(const PageRanges& ranges) { ranges_ = ranges; }
  const PageRanges& ranges() const { return ranges_; }

  void set_selection_only(bool selection_only) {
    selection_only_ = selection_only;
  }
  bool selection_only() const { return selection_only_; }

  void set_should_print_backgrounds(bool should_print_backgrounds) {
    should_print_backgrounds_ = should_print_backgrounds;
  }
  bool should_print_backgrounds() const { return should_print_backgrounds_; }

  void set_display_header_footer(bool display_header_footer) {
    display_header_footer_ = display_header_footer;
  }
  bool display_header_footer() const { return display_header_footer_; }

  void set_title(const base::string16& title) { title_ = title; }
  const base::string16& title() const { return title_; }

  void set_url(const base::string16& url) { url_ = url; }
  const base::string16& url() const { return url_; }

  void set_collate(bool collate) { collate_ = collate; }
  bool collate() const { return collate_; }

  void set_color(ColorModel color) { color_ = color; }
  ColorModel color() const { return color_; }

  void set_copies(int copies) { copies_ = copies; }
  int copies() const { return copies_; }

  void set_duplex_mode(DuplexMode duplex_mode) { duplex_mode_ = duplex_mode; }
  DuplexMode duplex_mode() const { return duplex_mode_; }

  int desired_dpi() const { return desired_dpi_; }

#if defined(OS_WIN)
  void set_print_text_with_gdi(bool use_gdi) { print_text_with_gdi_ = use_gdi; }
  bool print_text_with_gdi() const { return print_text_with_gdi_; }

  void set_printer_is_xps(bool is_xps) { printer_is_xps_ = is_xps; }
  bool printer_is_xps() const { return printer_is_xps_; }
#endif

  const base::string16& header_footer_html() const {
    return s_header_footer_html;
  }

  // Cookie generator. It is used to initialize PrintedDocument with its
  // associated PrintSettings, to be sure that each generated PrintedPage is
  // correctly associated with its corresponding PrintedDocument.
  static int NewCookie();

  // Sets the default value for header_footer_html and
  // print_background_graphics configuration.
  static void SetDefaultPrinterSettings(
      const base::string16& header_footer_html,
      bool print_background_graphics);

 private:
  // Multi-page printing. Each PageRange describes a from-to page combination.
  // This permits printing selected pages only.
  PageRanges ranges_;

  // Desired visible dots per inch rendering for output. Printing should be
  // scaled to ScreenDpi/dpix*desired_dpi.
  int desired_dpi_;

  // Indicates if the user only wants to print the current selection.
  bool selection_only_;

  // Indicates what kind of margins should be applied to the printable area.
  MarginType margin_type_;

  // Strings to be printed as headers and footers if requested by the user.
  base::string16 title_;
  base::string16 url_;

  // True if the user wants headers and footers to be displayed.
  bool display_header_footer_;

  // True if the user wants to print CSS backgrounds.
  bool should_print_backgrounds_;

  // True if the user wants to print with collate.
  bool collate_;

  // True if the user wants to print with collate.
  ColorModel color_;

  // Number of copies user wants to print.
  int copies_;

  // Duplex type user wants to use.
  DuplexMode duplex_mode_;

  // Printer device name as opened by the OS.
  base::string16 device_name_;

  // Media requested by the user.
  RequestedMedia requested_media_;

  // Page setup in device units.
  PageSetup page_setup_device_units_;

  // Printer's device effective dots per inch in both axis.
  int dpi_;

  // Scale factor
  double scale_factor_;

  // Is the orientation landscape or portrait.
  bool landscape_;

  // True if this printer supports AlphaBlend.
  bool supports_alpha_blend_;

#if defined(OS_WIN)
  // True to print text with GDI.
  bool print_text_with_gdi_;

  // True if the printer is an XPS printer.
  bool printer_is_xps_;
#endif

  // If margin type is custom, this is what was requested.
  PageMargins requested_custom_margins_in_points_;

  // The HTML content used to format header and footer of printed pages.
  static base::string16 s_header_footer_html;

  // Stores the default value of printing background graphics configuration.
  static bool s_print_background_graphics;
};

}  // namespace printing

#endif  // PRINTING_PRINT_SETTINGS_H_
