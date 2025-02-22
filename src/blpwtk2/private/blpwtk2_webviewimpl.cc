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

#include <blpwtk2_webviewimpl.h>

#include <blpwtk2_browsercontextimpl.h>
#include <blpwtk2_desktopstreamsregistry.h>
#include <blpwtk2_devtoolsfrontendhostdelegateimpl.h>
#include <blpwtk2_devtoolsmanagerdelegateimpl.h>
#include <blpwtk2_nativeviewwidget.h>
#include <blpwtk2_products.h>
#include <blpwtk2_renderwebcontentsview.h>
#include <blpwtk2_statics.h>
#include <blpwtk2_stringref.h>
#include <blpwtk2_webframeimpl.h>
#include <blpwtk2_webviewdelegate.h>
#include <blpwtk2_blob.h>
#include <blpwtk2_rendererutil.h>
#include <blpwtk2_webviewimplclient.h>
#include <blpwtk2_processhostimpl.h>

#include <base/bind.h>
#include <base/message_loop/message_loop.h>
#include <base/strings/utf_string_conversions.h>
#include <chrome/browser/printing/print_view_manager.h>
#include <components/printing/renderer/print_web_view_helper.h>
#include <content/browser/renderer_host/render_widget_host_view_base.h>
#include <content/public/browser/host_zoom_map.h>
#include <content/public/browser/media_capture_devices.h>
#include <content/public/browser/render_frame_host.h>

#include <content/public/browser/render_process_host.h>
#include <content/public/browser/render_view_host.h>
#include <content/public/browser/render_widget_host.h>
#include <content/public/browser/web_contents.h>
#include <content/public/browser/site_instance.h>
#include <content/public/common/file_chooser_file_info.h>
#include <content/public/common/web_preferences.h>
#include <content/public/renderer/render_view.h>
#include <content/renderer/render_view_impl.h>
#include <third_party/WebKit/public/web/WebFindOptions.h>
#include <third_party/WebKit/public/web/WebView.h>
#include <ui/base/win/hidden_window.h>
#include <errno.h>

namespace blpwtk2 {

static const content::MediaStreamDevice *findDeviceById(
    const std::string& id,
    const content::MediaStreamDevices& devices)
{
    for (std::size_t i = 0; i < devices.size(); ++i) {
        if (id == devices[i].id) {
            return &devices[i];
        }
    }
    return 0;
}

static std::set<WebViewImpl*> g_instances;


                        // -----------------
                        // class WebViewImpl
                        // -----------------

WebViewImpl::WebViewImpl(WebViewDelegate          *delegate,
                         blpwtk2::NativeView       parent,
                         BrowserContextImpl       *browserContext,
                         int                       hostAffinity,
                         bool                      initiallyVisible,
                         bool                      rendererUI,
                         const WebViewProperties&  properties)
    : d_delegate(delegate)
    , d_implClient(0)
    , d_renderViewHost(nullptr)
    , d_browserContext(browserContext)
    , d_widget(0)
    , d_properties(properties)
    , d_isReadyForDelete(false)
    , d_wasDestroyed(false)
    , d_isDeletingSoon(false)
    , d_ncHitTestEnabled(false)
    , d_ncHitTestPendingAck(false)
    , d_altDragRubberbandingEnabled(false)
    , d_lastNCHitTestResult(HTCLIENT)
    , d_hostId(hostAffinity)
    , d_rendererUI(rendererUI)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(browserContext);

    g_instances.insert(this);
    d_browserContext->incrementWebViewCount();

    content::WebContents::CreateParams createParams(browserContext);
    createParams.render_process_affinity = hostAffinity;

    if (rendererUI) {
        auto web_contents_view = new RenderWebContentsView();
        createParams.host = web_contents_view;
        createParams.render_view_host_delegate_view = web_contents_view;
    }

    d_webContents.reset(content::WebContents::Create(createParams));
    d_webContents->SetDelegate(this);
    Observe(d_webContents.get());

    CR_DEFINE_STATIC_LOCAL(const gfx::FontRenderParams, fontRenderParams,
        (gfx::GetFontRenderParams(gfx::FontRenderParamsQuery(), NULL)));

    content::RendererPreferences *prefs = d_webContents->GetMutableRendererPrefs();

    prefs->should_antialias_text    = fontRenderParams.antialiasing;
    prefs->use_subpixel_positioning = fontRenderParams.subpixel_positioning;
    prefs->hinting                  = fontRenderParams.hinting;
    prefs->use_autohinter           = fontRenderParams.autohinter;
    prefs->use_bitmaps              = fontRenderParams.use_bitmaps;
    prefs->subpixel_rendering       = fontRenderParams.subpixel_rendering;

    printing::PrintViewManager::CreateForWebContents(d_webContents.get());

    createWidget(parent);

    if (initiallyVisible) {
        show();
    }
}

WebViewImpl::~WebViewImpl()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(d_wasDestroyed);
    DCHECK(d_isReadyForDelete);
    DCHECK(d_isDeletingSoon);

    g_instances.erase(this);

    if (d_widget) {
        d_widget->setDelegate(0);
        d_widget->destroy();
    }
}

void WebViewImpl::setImplClient(WebViewImplClient *client)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(client);
    DCHECK(!d_implClient);
    d_implClient = client;
    if (d_widget) {
        d_implClient->updateNativeViews(d_widget->getNativeWidgetView(), ui::GetHiddenWindow());
    }
}

gfx::NativeView WebViewImpl::getNativeView() const
{
    DCHECK(Statics::isInBrowserMainThread());
    return d_webContents->GetNativeView();
}

void WebViewImpl::showContextMenu(const ContextMenuParams& params)
{
    DCHECK(Statics::isInBrowserMainThread());
    if (d_wasDestroyed) {
        return;
    }
    if (d_delegate) {
        d_delegate->showContextMenu(this, params);
    }
}

void WebViewImpl::saveCustomContextMenuContext(
    content::RenderFrameHost                 *rfh,
    const content::CustomContextMenuContext&  context)
{
    d_customContext = context;
}

void WebViewImpl::handleFindRequest(const FindOnPageRequest& request)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);

    blink::WebFindOptions options;
    options.findNext = request.findNext;
    options.forward = request.forward;
    options.matchCase = request.matchCase;
    d_webContents->Find(request.reqId,
                        base::UTF8ToUTF16(request.text),
                        options);
}

void WebViewImpl::overrideWebkitPrefs(content::WebPreferences *prefs)
{
    prefs->dom_paste_enabled = d_properties.domPasteEnabled;
    prefs->javascript_can_access_clipboard = d_properties.javascriptCanAccessClipboard;
    prefs->navigate_on_drag_drop = false;
}

void WebViewImpl::onRenderViewHostMadeCurrent(content::RenderViewHost *renderViewHost)
{
    d_renderViewHost = renderViewHost;

    int routingId = getRoutingId();
    if (routingId >= 0 && d_implClient) {
        d_implClient->gotNewRenderViewRoutingId(routingId);
    }

#ifdef BB_RENDER_VIEW_HOST_SUPPORTS_RUBBERBANDING
    renderViewHost->EnableAltDragRubberbanding(d_altDragRubberbandingEnabled);
#endif
}

void WebViewImpl::destroy()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    DCHECK(!d_isDeletingSoon);

    d_browserContext->decrementWebViewCount();

    Observe(0);  // stop observing the WebContents
    d_webContents.reset();
    d_wasDestroyed = true;
    if (d_isReadyForDelete) {
        d_isDeletingSoon = true;
        base::MessageLoop::current()->task_runner()->DeleteSoon(FROM_HERE, this);
    }
}

WebFrame *WebViewImpl::mainFrame()
{
    NOTREACHED() << "mainFrame() not supported in WebViewImpl";
    return nullptr;
}

int WebViewImpl::loadUrl(const StringRef& url)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    std::string surl(url.data(), url.length());
    GURL gurl(surl);
    if (!gurl.has_scheme()) {
        gurl = GURL("http://" + surl);
    }

    d_webContents->GetController().LoadURL(
        gurl,
        content::Referrer(),
        ui::PageTransitionFromInt(ui::PAGE_TRANSITION_TYPED | ui::PAGE_TRANSITION_FROM_ADDRESS_BAR),
        std::string());

    return 0;
}

void WebViewImpl::drawContentsToBlob(Blob *blob, const DrawParams& params)
{
    NOTREACHED() << "drawContentsToBlob() not supported in WebViewImpl";
}

String WebViewImpl::printToPDF(const StringRef& propertyName)
{
    NOTREACHED() << "printToPDF() not supported in WebViewImpl";
    return String();
}

int WebViewImpl::getRoutingId() const
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(d_renderViewHost);
    if (d_wasDestroyed) {
        return -1;
    }

    return d_renderViewHost->GetRoutingID();
}

#define GetAValue(argb)      (LOBYTE((argb)>>24))

void WebViewImpl::setBackgroundColor(NativeColor color)
{
    d_webContents->GetRenderViewHost()->GetWidget()->GetView()->SetBackgroundColor(
        SkColorSetARGB(
            GetAValue(color),
            GetRValue(color),
            GetGValue(color),
            GetBValue(color)
    ));
}

void WebViewImpl::setRegion(NativeRegion region)
{
    if (d_widget) {
        d_widget->setRegion(region);
    }
}

void WebViewImpl::clearTooltip()
{
    content::RenderWidgetHostViewBase *rwhv =
        static_cast<content::RenderWidgetHostViewBase*>(
            d_webContents->GetRenderWidgetHostView());

    rwhv->SetTooltipText(L"");
}

void WebViewImpl::rootWindowCompositionChanged()
{
    if (d_widget) {
        d_widget->compositionChanged();
    }
}

v8::MaybeLocal<v8::Value> WebViewImpl::callFunction(v8::Local<v8::Function>  func,
                                                    v8::Local<v8::Value>     recv,
                                                    int                      argc,
                                                    v8::Local<v8::Value>    *argv)
{
    NOTREACHED() << "callFunction() not supported in WebViewImpl";
    return v8::MaybeLocal<v8::Value>();
}

static GURL GetDevToolsFrontendURL()
{
    int port = DevToolsManagerDelegateImpl::GetHttpHandlerPort();
    return GURL(base::StringPrintf(
        "http://127.0.0.1:%d/devtools/inspector.html", port));
}

void WebViewImpl::loadInspector(unsigned int pid, int routingId)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    DCHECK(Statics::hasDevTools) << "Could not find: " << BLPWTK2_PAK_NAME;

    int hostId = 0;
    BrowserContextImpl *context = nullptr;

    ProcessHostImpl::getHostId(
            &hostId, &context, static_cast<base::ProcessId>(pid));

    for (const auto& webView : g_instances) {
        if (webView->d_hostId == hostId &&
            webView->getRoutingId() == routingId) {

            content::WebContents *inspectedContents =
                webView->d_webContents.get();
            DCHECK(inspectedContents);

            d_devToolsFrontEndHost.reset(
                new DevToolsFrontendHostDelegateImpl(d_webContents.get(),
                                                     inspectedContents));

            GURL url = GetDevToolsFrontendURL();
            loadUrl(url.spec());
            LOG(INFO) << "Loaded devtools for routing id: " << routingId;
            return;
        }
    }

    LOG(WARNING) << "Failed to load devtools. Could not found a RenderView with routing id: " << routingId;
}

void WebViewImpl::inspectElementAt(const POINT& point)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    DCHECK(d_devToolsFrontEndHost.get())
        << "Need to call loadInspector first!";
    d_devToolsFrontEndHost->inspectElementAt(point);
}

int WebViewImpl::goBack()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    if (d_webContents->GetController().CanGoBack()) {
        d_webContents->GetController().GoBack();
        return 0;
    }

    return EINVAL;
}

int WebViewImpl::goForward()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    if (d_webContents->GetController().CanGoForward()) {
        d_webContents->GetController().GoForward();
        return 0;
    }

    return EINVAL;
}

int WebViewImpl::reload()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);

    // TODO: do we want to make this an argument
    const bool checkForRepost = false;

    d_webContents->GetController().Reload(checkForRepost);
    return 0;
}

void WebViewImpl::stop()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    d_webContents->Stop();
}

void WebViewImpl::takeKeyboardFocus()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    if (d_widget) {
        d_widget->focus();
    }
}

void WebViewImpl::setLogicalFocus(bool focused)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    if (focused) {
        d_webContents->Focus();
    }
    else {
        d_webContents->GetRenderWidgetHostView()->Blur();
    }
}

void WebViewImpl::show()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    if (!d_widget) {
        createWidget(ui::GetHiddenWindow());
    }
    if (d_widget)
        d_widget->show();
}

void WebViewImpl::hide()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    if (!d_widget) {
        createWidget(ui::GetHiddenWindow());
    }
    if (d_widget)
        d_widget->hide();
}

void WebViewImpl::setParent(NativeView parent)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);

    if (!parent) {
        parent = ui::GetHiddenWindow();
    }

    if (!d_widget) {
        createWidget(parent);
    }
    else {
        d_widget->setParent(parent);
    }
}

void WebViewImpl::move(int left, int top, int width, int height)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    if (!d_widget) {
        createWidget(ui::GetHiddenWindow());
    }

    if (d_widget)
        d_widget->move(left, top, width, height);
}

void WebViewImpl::cutSelection()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    d_webContents->Cut();
}

void WebViewImpl::copySelection()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    d_webContents->Copy();
}

void WebViewImpl::paste()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    d_webContents->Paste();
}

void WebViewImpl::deleteSelection()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    d_webContents->Delete();
}

void WebViewImpl::enableNCHitTest(bool enabled)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    d_ncHitTestEnabled = enabled;
    d_lastNCHitTestResult = HTCLIENT;
}

void WebViewImpl::onNCHitTestResult(int x, int y, int result)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    DCHECK(d_ncHitTestPendingAck);
    d_lastNCHitTestResult = result;
    d_ncHitTestPendingAck = false;

    // Re-request it if the mouse position has changed, so that we
    // always have the latest info.
    if (d_delegate && d_ncHitTestEnabled) {
        POINT ptNow;
        if(::GetCursorPos(&ptNow)) {
            if (ptNow.x != x || ptNow.y != y) {
                d_ncHitTestPendingAck = true;
                d_delegate->requestNCHitTest(this);
            }
        }
    }
}

void WebViewImpl::performCustomContextMenuAction(int actionId)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    d_webContents->ExecuteCustomContextMenuCommand(actionId, d_customContext);
}

void WebViewImpl::enableAltDragRubberbanding(bool enabled)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    d_altDragRubberbandingEnabled = enabled;

#ifdef BB_RENDER_VIEW_HOST_SUPPORTS_RUBBERBANDING
    if (d_webContents->GetRenderViewHost()) {
        d_webContents->GetRenderViewHost()->EnableAltDragRubberbanding(enabled);
    }
#endif
}

bool WebViewImpl::forceStartRubberbanding(int x, int y)
{
    NOTREACHED() << "forceStartRubberbanding() not supported in WebViewImpl";
    return false;
}

bool WebViewImpl::isRubberbanding() const
{
    NOTREACHED() << "isRubberbanding() not supported in WebViewImpl";
    return false;
}

void WebViewImpl::abortRubberbanding()
{
    NOTREACHED() << "abortRubberbanding() not supported in WebViewImpl";
}

String WebViewImpl::getTextInRubberband(const NativeRect& rect)
{
    NOTREACHED() << "getTextInRubberband() not supported in WebViewImpl";
    return String();
}

void WebViewImpl::find(const StringRef& text, bool matchCase, bool forward)
{
    DCHECK(Statics::isOriginalThreadMode())
        <<  "renderer-main thread mode should use handleFindRequest";

    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);

    if (!d_find) {
        d_find.reset(new FindOnPage());
    }

    handleFindRequest(
            d_find->makeRequest(std::string(text.data(), text.size()),
                                matchCase,
                                forward));
}

void WebViewImpl::stopFind(bool preserveSelection)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);

    if (preserveSelection) {
        d_webContents->StopFinding(content::STOP_FIND_ACTION_ACTIVATE_SELECTION);
    }
    else {
        d_webContents->StopFinding(content::STOP_FIND_ACTION_CLEAR_SELECTION);
    }
}

void WebViewImpl::replaceMisspelledRange(const StringRef& text)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    base::string16 text16;
    base::UTF8ToUTF16(text.data(), text.length(), &text16);
    d_webContents->ReplaceMisspelling(text16);
}

void WebViewImpl::rootWindowPositionChanged()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    content::RenderWidgetHostViewBase *rwhv =
        static_cast<content::RenderWidgetHostViewBase*>(
            d_webContents->GetRenderWidgetHostView());
    if (rwhv && !d_rendererUI)
        rwhv->UpdateScreenInfo(rwhv->GetNativeView());
}

void WebViewImpl::rootWindowSettingsChanged()
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(!d_wasDestroyed);
    content::RenderWidgetHostViewBase *rwhv =
        static_cast<content::RenderWidgetHostViewBase*>(
            d_webContents->GetRenderWidgetHostView());
    if (rwhv && !d_rendererUI)
        rwhv->UpdateScreenInfo(rwhv->GetNativeView());
}

void WebViewImpl::handleInputEvents(const InputEvent *events, size_t eventsCount)
{
    NOTREACHED() << "handleInputEvents() not supported in WebViewImpl";
}

void WebViewImpl::setDelegate(blpwtk2::WebViewDelegate *delegate)
{
    DCHECK(Statics::isInBrowserMainThread());
    d_delegate = delegate;
}

void WebViewImpl::createWidget(blpwtk2::NativeView parent)
{
    DCHECK(!d_widget);
    DCHECK(!d_wasDestroyed);

    if (d_rendererUI) {
        return;
    }

    // This creates the HWND that will host the WebContents.  The widget
    // will be deleted when the HWND is destroyed.
    d_widget = new blpwtk2::NativeViewWidget(
        d_webContents->GetNativeView(),
        parent,
        this,
        d_properties.activateWindowOnMouseDown,
        d_properties.rerouteMouseWheelToAnyRelatedWindow);

    if (d_implClient) {
        d_implClient->updateNativeViews(d_widget->getNativeWidgetView(), ui::GetHiddenWindow());
    }
}

void WebViewImpl::onDestroyed(NativeViewWidget *source)
{
    DCHECK(source == d_widget);
    d_widget = 0;
}

void WebViewImpl::DidNavigateMainFramePostCommit(content::WebContents *source)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(source == d_webContents.get());
    d_isReadyForDelete = true;
    if (d_wasDestroyed) {
        if (!d_isDeletingSoon) {
            d_isDeletingSoon = true;
            base::MessageLoop::current()->task_runner()->DeleteSoon(FROM_HERE, this);
        }
    }
}

bool WebViewImpl::TakeFocus(content::WebContents *source, bool reverse)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(source == d_webContents.get());
    if (d_wasDestroyed || !d_delegate) {
        return false;
    }
    if (reverse) {
        return false;
    }
    return false;
}

void WebViewImpl::RequestMediaAccessPermission(
    content::WebContents                  *web_contents,
    const content::MediaStreamRequest&     request,
    const content::MediaResponseCallback&  callback)
{
    class DummyMediaStreamUI final : public content::MediaStreamUI {
      public:
        gfx::NativeViewId OnStarted(const base::Closure& stop) override
        {
            return 0;
        }
    };

    const content::MediaStreamDevices& audioDevices =
        content::MediaCaptureDevices::GetInstance()->GetAudioCaptureDevices();
    const content::MediaStreamDevices& videoDevices =
        content::MediaCaptureDevices::GetInstance()->GetVideoCaptureDevices();

    std::unique_ptr<content::MediaStreamUI> ui(new DummyMediaStreamUI());
    content::MediaStreamDevices devices;
    if (request.requested_video_device_id.empty()) {
        if (request.video_type != content::MEDIA_NO_SERVICE && !videoDevices.empty()) {
            devices.push_back(videoDevices[0]);
        }
    }
    else {
        const content::MediaStreamDevice *device = findDeviceById(request.requested_video_device_id, videoDevices);
        if (device) {
            devices.push_back(*device);
        }
        else {
            content::MediaStreamDevice desktop_device = DesktopStreamsRegistry::GetInstance()->RequestMediaForStreamId(request.requested_video_device_id);
            if (desktop_device.type != content::MEDIA_NO_SERVICE) {
                devices.push_back(desktop_device);
            }
        }
    }
    if (request.requested_audio_device_id.empty()) {
        if (request.audio_type != content::MEDIA_NO_SERVICE && !audioDevices.empty()) {
            devices.push_back(audioDevices[0]);
        }
    }
    else {
        const content::MediaStreamDevice *device = findDeviceById(request.requested_audio_device_id, audioDevices);
        if (device) {
            devices.push_back(*device);
        }
    }
    callback.Run(devices, content::MEDIA_DEVICE_OK, std::move(ui));
}

bool WebViewImpl::CheckMediaAccessPermission(content::WebContents *,
                                             const GURL&,
                                             content::MediaStreamType)
{
    // When CheckMediaAccessPermission returns true,
    // the user will be able to access MediaDeviceInfo.label
    // (for example "External USB Webcam"), while enumerating media devices
    // https://developer.mozilla.org/en-US/docs/Web/API/MediaDeviceInfo
    // Also the user will be allowed to set the audio output device to a
    // HTMLMediaElement
    // https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/setSinkId
    return true;
}

bool WebViewImpl::OnNCHitTest(int *result)
{
    if (d_ncHitTestEnabled && d_delegate) {
        if (!d_ncHitTestPendingAck) {
            d_ncHitTestPendingAck = true;
            d_delegate->requestNCHitTest(this);
        }

        // Windows treats HTBOTTOMRIGHT in a 'special' way when a child window
        // (i.e. this WebView's hwnd) overlaps with the bottom-right 3x3 corner
        // of the parent window.  In this case, subsequent messages like
        // WM_SETCURSOR and other WM_NC* messages get routed to the parent
        // window instead of the child window.
        // To work around this, we will lie to Windows when the app returns
        // HTBOTTOMRIGHT.  We'll return HTOBJECT instead.  AFAICT, HTOBJECT is
        // a completely unused hit-test code.  We'll forward HTOBJECT events to
        // the app as HTBOTTOMRIGHT (see further below).
        if (HTBOTTOMRIGHT == d_lastNCHitTestResult) {
            *result = HTOBJECT;
        }
        else {
            *result = d_lastNCHitTestResult;
        }

        return true;
    }
    return false;
}

bool WebViewImpl::OnNCDragBegin(int hitTestCode)
{
    if (!d_ncHitTestEnabled || !d_delegate) {
        return false;
    }

    // See explanation in 'OnNCHitTest' above.
    if (HTOBJECT == hitTestCode) {
        hitTestCode = HTBOTTOMRIGHT;
    }

    POINT screenPoint;
    switch (hitTestCode) {
    case HTCAPTION:
    case HTLEFT:
    case HTTOP:
    case HTRIGHT:
    case HTBOTTOM:
    case HTTOPLEFT:
    case HTTOPRIGHT:
    case HTBOTTOMRIGHT:
    case HTBOTTOMLEFT:
        ::GetCursorPos(&screenPoint);
        d_delegate->ncDragBegin(this, hitTestCode, screenPoint);
        return true;
    default:
        return false;
    }
}

void WebViewImpl::OnNCDragMove()
{
    if (d_delegate) {
        POINT screenPoint;
        ::GetCursorPos(&screenPoint);
        d_delegate->ncDragMove(this, screenPoint);
    }
}

void WebViewImpl::OnNCDragEnd()
{
    if (d_delegate) {
        POINT screenPoint;
        ::GetCursorPos(&screenPoint);
        d_delegate->ncDragEnd(this, screenPoint);
    }
}

void WebViewImpl::OnNCDoubleClick()
{
    if (d_delegate) {
        POINT screenPoint;
        ::GetCursorPos(&screenPoint);
        d_delegate->ncDoubleClick(this, screenPoint);
    }
}

aura::Window *WebViewImpl::GetDefaultActivationWindow()
{
    DCHECK(Statics::isInBrowserMainThread());
    content::RenderWidgetHostView *rwhv = d_webContents->GetRenderWidgetHostView();
    if (rwhv && !d_rendererUI) {
        return rwhv->GetNativeView();
    }
    return nullptr;
}

bool WebViewImpl::ShouldSetKeyboardFocusOnMouseDown()
{
    DCHECK(Statics::isInBrowserMainThread());
    return d_properties.takeKeyboardFocusOnMouseDown;
}

bool WebViewImpl::ShouldSetLogicalFocusOnMouseDown()
{
    DCHECK(Statics::isInBrowserMainThread());
    return d_properties.takeLogicalFocusOnMouseDown;
}

void WebViewImpl::FindReply(content::WebContents *source_contents,
                            int                   request_id,
                            int                   number_of_matches,
                            const gfx::Rect&      selection_rect,
                            int                   active_match_ordinal,
                            bool                  final_update)
{
    DCHECK(Statics::isInBrowserMainThread());
    DCHECK(source_contents == d_webContents.get());
    DCHECK(d_implClient || d_find.get());

    if (d_wasDestroyed) {
        return;
    }

    if (d_implClient) {
        d_implClient->findStateWithReqId(request_id,
                                         number_of_matches,
                                         active_match_ordinal,
                                         final_update);
    }
    else if (d_delegate && d_find->applyUpdate(request_id,
                                               number_of_matches,
                                               active_match_ordinal)) {
        d_delegate->findState(this,
                              d_find->numberOfMatches(),
                              d_find->activeMatchIndex(),
                              final_update);
    }
}

// WebContentsObserver overrides
void WebViewImpl::RenderViewCreated(content::RenderViewHost *render_view_host)
{
    onRenderViewHostMadeCurrent(render_view_host);
}

void WebViewImpl::RenderViewHostChanged(content::RenderViewHost *old_host,
                                        content::RenderViewHost *new_host)
{
    onRenderViewHostMadeCurrent(new_host);
}

void WebViewImpl::DidFinishLoad(content::RenderFrameHost *render_frame_host,
                                const GURL&               validated_url)
{
    DCHECK(Statics::isInBrowserMainThread());
    if (d_wasDestroyed || !d_delegate) {
        return;
    }

    // Only call didFinishLoad() for the main frame.
    if (!render_frame_host->GetParent()) {
        d_delegate->didFinishLoad(this, validated_url.spec());
    }
}

void WebViewImpl::DidFailLoad(content::RenderFrameHost *render_frame_host,
                              const GURL&               validated_url,
                              int                       error_code,
                              const base::string16&     error_description,
                              bool                      was_ignored_by_handler)
{
    DCHECK(Statics::isInBrowserMainThread());
    if (d_wasDestroyed || !d_delegate) {
        return;
    }

    // Only call DidFailLoad() for the main frame.
    if (!render_frame_host->GetParent()) {
        d_delegate->didFailLoad(this, validated_url.spec());
    }
}

void WebViewImpl::OnWebContentsFocused()
{
    DCHECK(Statics::isInBrowserMainThread());
    if (d_wasDestroyed) return;
    if (d_delegate)
        d_delegate->focused(this);
}

void WebViewImpl::OnWebContentsBlurred()
{
    DCHECK(Statics::isInBrowserMainThread());
    if (d_wasDestroyed) return;
    if (d_delegate)
        d_delegate->blurred(this);
}

}  // close namespace blpwtk2

// vim: ts=4 et

