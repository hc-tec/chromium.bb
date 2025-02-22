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

#include <blpwtk2_toolkitfactory.h>

#include <blpwtk2_products.h>
#include <blpwtk2_statics.h>
#include <blpwtk2_stringref.h>
#include <blpwtk2_toolkitcreateparams.h>
#include <blpwtk2_toolkitimpl.h>
#include <blpwtk2_fontcollectionimpl.h>

#include <base/environment.h>
#include <base/files/file_path.h>
#include <base/logging.h>  // for DCHECK
#include <base/strings/string16.h>
#include <base/strings/utf_string_conversions.h>
#include <base/win/wrapped_window_proc.h>
#include <components/printing/renderer/print_web_view_helper.h>
#include <content/child/font_warmup_win.h>
#include <content/public/app/content_main_runner.h>
#include <content/renderer/render_frame_impl.h>
#include <content/renderer/render_widget.h>
#include <net/http/http_network_session.h>
#include <net/socket/client_socket_pool_manager.h>
#include <printing/print_settings.h>
#include <third_party/WebKit/public/web/WebKit.h>
#include <ui/views/corewm/tooltip_win.h>

namespace blpwtk2 {
static bool g_created = false;
static ToolkitCreateParams::LogMessageHandler g_logMessageHandler = nullptr;
static ToolkitCreateParams::ConsoleLogMessageHandler g_consoleLogMessageHandler = nullptr;

static void setMaxSocketsPerProxy(int count)
{
    DCHECK(1 <= count);
    DCHECK(99 >= count);

    const net::HttpNetworkSession::SocketPoolType POOL =
        net::HttpNetworkSession::NORMAL_SOCKET_POOL;

    // The max per group can never exceed the max per proxy.  Use the default
    // max per group, unless count is less than the default.

    int prevMaxPerProxy =
        net::ClientSocketPoolManager::max_sockets_per_proxy_server(POOL);
    int newMaxPerGroup = std::min(count,
                                  (int)net::kDefaultMaxSocketsPerGroupNormal);

    if (newMaxPerGroup > prevMaxPerProxy) {
        net::ClientSocketPoolManager::set_max_sockets_per_proxy_server(
            POOL,
            count);
        net::ClientSocketPoolManager::set_max_sockets_per_group(
            POOL,
            newMaxPerGroup);
    }
    else {
        net::ClientSocketPoolManager::set_max_sockets_per_group(
            POOL,
            newMaxPerGroup);
        net::ClientSocketPoolManager::set_max_sockets_per_proxy_server(
            POOL,
            count);
    }
}

						// ---------------------
						// struct ToolkitFactory
						// ---------------------
static ToolkitCreateParams::LogMessageSeverity decodeLogSeverity(int severity)
{
    switch (severity) {
    case logging::LOG_INFO:
        return ToolkitCreateParams::kSeverityInfo;
    case logging::LOG_WARNING:
        return ToolkitCreateParams::kSeverityWarning;
    case logging::LOG_ERROR:
        return ToolkitCreateParams::kSeverityError;
    case logging::LOG_FATAL:
        return ToolkitCreateParams::kSeverityFatal;
    default:
        return ToolkitCreateParams::kSeverityVerbose;
    }
}

static bool wtk2LogMessageHandlerFunction(int severity,
                                          const char* file,
                                          int line,
                                          size_t message_start,
                                          const std::string& str)
{
    g_logMessageHandler(decodeLogSeverity(severity), file, line, str.c_str() + message_start);
    return true;
}

static void wtk2ConsoleLogMessageHandlerFunction(int severity,
                                                 const std::string& file,
                                                 int line,
                                                 int column,
                                                 const std::string& message,
                                                 const std::string& stack_trace)
{
    g_consoleLogMessageHandler(decodeLogSeverity(severity),
                               StringRef(file.data(), file.length()),
                               line,
                               column,
                               StringRef(message.data(), message.length()),
                               StringRef(stack_trace.data(), stack_trace.length()));
}

// static
Toolkit* ToolkitFactory::create(const ToolkitCreateParams& params)
{
    DCHECK(!g_created);
    DCHECK(!ToolkitImpl::instance());

    Statics::initApplicationMainThread();
    Statics::threadMode = params.threadMode();
    Statics::inProcessResourceLoader = params.inProcessResourceLoader();
    Statics::isInProcessRendererEnabled = params.isInProcessRendererEnabled();
    Statics::channelErrorHandler = params.channelErrorHandler();
    Statics::inProcessResizeOptimizationDisabled = params.isInProcessResizeOptimizationDisabled();
    Statics::rendererUIEnabled = params.rendererUIEnabled();

    // If this process is the host, then set the environment variable that
    // subprocesses will use to determine which SubProcessMain module should
    // be loaded.
    if (params.hostChannel().isEmpty()) {
        char subProcessModuleEnvVar[64];
        sprintf_s(subProcessModuleEnvVar, sizeof(subProcessModuleEnvVar),
                  "BLPWTK2_SUBPROCESS_%d_%d_%d_%d_%d",
                  CHROMIUM_VERSION_MAJOR,
                  CHROMIUM_VERSION_MINOR,
                  CHROMIUM_VERSION_BUILD,
                  CHROMIUM_VERSION_PATCH,
                  BB_PATCH_NUMBER);
        std::string subProcessModule = params.subProcessModule().toStdString();
        if (subProcessModule.empty()) {
            subProcessModule = BLPWTK2_DLL_NAME;
        }
        std::unique_ptr<base::Environment> env(base::Environment::Create());
        env->SetVar(subProcessModuleEnvVar, subProcessModule);
    }

    g_logMessageHandler = params.logMessageHandler();
    if (g_logMessageHandler) {
        logging::SetWtk2LogMessageHandler(wtk2LogMessageHandlerFunction);
    }

    g_consoleLogMessageHandler = params.consoleLogMessageHandler();
    if (g_consoleLogMessageHandler) {
        content::RenderFrameImpl::SetConsoleLogMessageHandler(wtk2ConsoleLogMessageHandlerFunction);
    }
    NativeColor activeSearchColor = params.activeTextSearchHighlightColor();
    NativeColor inactiveSearchColor = params.inactiveTextSearchHighlightColor();
    blink::setTextSearchHighlightColor(GetRValue(activeSearchColor), GetGValue(activeSearchColor), GetBValue(activeSearchColor),
                                       GetRValue(inactiveSearchColor), GetGValue(inactiveSearchColor), GetBValue(inactiveSearchColor));

    NativeColor activeSearchTextColor = params.activeTextSearchColor();
    blink::setTextSearchColor(GetRValue(activeSearchTextColor), GetGValue(activeSearchTextColor), GetBValue(activeSearchTextColor));
    views::corewm::TooltipWin::SetTooltipStyle(params.tooltipFont());

    base::win::SetWinProcExceptionFilter(params.winProcExceptionFilter());

    content::ContentMainRunner::SetCRTErrorHandlerFunctions(
        params.invalidParameterHandler(),
        params.purecallHandler());

    DCHECK(!Statics::inProcessResourceLoader ||
            Statics::isRendererMainThreadMode());

    if (params.isMaxSocketsPerProxySet()) {
        setMaxSocketsPerProxy(params.maxSocketsPerProxy());
    }

    std::vector<std::string> commandLineSwitches;

    for (size_t i = 0; i < params.numCommandLineSwitches(); ++i) {
        StringRef switchRef = params.commandLineSwitchAt(i);
        std::string switchString(switchRef.data(), switchRef.length());
        commandLineSwitches.push_back(switchString);
    }

    std::string dictionaryPath(params.dictionaryPath().data(),
                               params.dictionaryPath().length());
    std::string hostChannel(params.hostChannel().data(),
                            params.hostChannel().length());
    std::string profileDirectory(params.profileDirectory().data(),
                                 params.profileDirectory().length());

    std::string html(params.headerFooterHTMLContent().data(),
                     params.headerFooterHTMLContent().length());
    printing::PrintSettings::SetDefaultPrinterSettings(
        base::UTF8ToUTF16(html), params.isPrintBackgroundGraphicsEnabled());

    ToolkitImpl* toolkit = new ToolkitImpl(dictionaryPath,
                                           hostChannel,
                                           commandLineSwitches,
                                           params.isIsolatedProfile(),
                                           profileDirectory);

    std::vector<std::wstring> font_files;

    for (size_t i = 0; i < params.numSideLoadedFonts(); ++i) {
        StringRef fontFileRef = params.sideLoadedFontAt(i);
		std::wstring font_filename;
		base::UTF8ToWide(fontFileRef.data(), fontFileRef.length(), &font_filename);
		font_files.push_back(font_filename);
    }

    if (params.numSideLoadedFonts() > 0) {
        FontCollectionImpl::GetCurrent()->SetCustomFonts(std::move(font_files));
    }

    g_created = true;
    return toolkit;
}

}  // close namespace blpwtk2

// vim: ts=4 et

