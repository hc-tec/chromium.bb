/*
 * Copyright (C) 2014 Bloomberg Finance L.P.
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

#include <blpwtk2_processhostimpl.h>

#include <blpwtk2_browsercontextimpl.h>
#include <blpwtk2_channelinfo.h>
#include <blpwtk2_desktopstreamsregistry.h>
#include <blpwtk2_products.h>
#include <blpwtk2_statics.h>
#include <blpwtk2_toolkit.h>
#include <blpwtk2_webviewhostimpl.h>
#include <blpwtk2_webviewhostobserver.h>
#include <blpwtk2_utility.h>
#include <blpwtk2/private/blpwtk2_webview.mojom.h>

#include <content/browser/renderer_host/render_process_host_impl.h>
#include <mojo/edk/embedder/platform_channel_pair.h>
#include <mojo/edk/embedder/embedder.h>
#include <mojo/public/cpp/bindings/strong_binding.h>
#include <mojo/public/cpp/bindings/interface_request.h>
#include <content/public/browser/browser_thread.h>
#include <printing/backend/print_backend.h>
 
// TODO(imran): This helper function is stolen from a newer version of Mojo.
// Remove it when it becomes available in
// mojo/public/cpp/bindings/interface_request.h
namespace mojo {
template <typename Interface>
InterfaceRequest<Interface> MakeRequest(
        InterfacePtr<Interface>                     *ptr,
        scoped_refptr<base::SingleThreadTaskRunner>  runner = nullptr)
{
    MessagePipe pipe;
    ptr->Bind(InterfacePtrInfo<Interface>(std::move(pipe.handle0), 0u),
              std::move(runner));

    InterfaceRequest<Interface> request;
    request.Bind(std::move(pipe.handle1));
    return request;
}
}

namespace blpwtk2 {

namespace {
std::map<std::string, scoped_refptr<BrowserContextImpl> > g_browserContexts;

scoped_refptr<BrowserContextImpl> getBrowserContext(
        bool isolated, const std::string& profileDir)
{
    // Make sure 'isolated' flag is disabled when a profile directory is
    // specified.  We don't want two contexts to fight over the same directory
    // to store the profile data.
    if (!profileDir.empty()) {
        DCHECK(!isolated);
        isolated = false;
    }

    if (!isolated) {
        auto context = g_browserContexts[profileDir];
        if (!context) {
            context = new BrowserContextImpl(profileDir);
        }

        return context;
    }

    return new BrowserContextImpl(profileDir);
}

void releaseBrowserContext(scoped_refptr<BrowserContextImpl>&& context)
{
    // Find the map iterator that corresponds to the specified 'context'.
    // This lookup is somewhat expensive but this method usually runs during
    // shutdown so there is little need to optimize it.
    for (auto it = g_browserContexts.begin();
         it != g_browserContexts.end();
         ++it) {
        if (it->second == context) {
            // At this point, the 'g_browserContexts' map should have one
            // reference to the browser context and the passed in 'context'
            // should have another reference.  We release the reference
            // that was passed in.
            context = nullptr;

            // Now the 'g_browserContexts' map has one reference and possibly
            // another instance of ProcessHostImpl::Impl *may* have another
            // reference.  We test this by checking if the browser context has
            // only one reference.  If it checks out, we know that no other
            // ProcessHostImpl::Impl has a reference to this browser context
            // and that we can remove this entry from the global map.
            if (it->second->HasOneRef()) {
                // No other ProcessHostImpl::Impl instance has a reference to
                // tis browser context so we can remove it from the global
                // map.
                g_browserContexts.erase(it);
            }

            break;
        }
    }
}

} // close anonymous namespace

                        // ---------------------------
                        // class ProcessHostImpl::Impl
                        // ---------------------------

ProcessHostImpl::Impl::Impl(bool isolated, const std::string& profileDir)
    : d_processId(0)
    , d_context(getBrowserContext(isolated, profileDir))
    , d_renderProcessHost(content::RenderProcessHost::CreateProcessHost(
                base::GetCurrentProcessHandle(), d_context.get()))
    , d_processHandle(0)
{
    // Initialize the RenderProcessHost.  This will register all the Mojo
    // services provided by RenderProcessHost and will call back to the
    // ChromeContentClient to register external services.  In this case,
    // the ChromeContentClient is blpwtk2::ContentBrowserClientImpl.  When
    // ContentBrowserClientImpl gets called to register the external services,
    // it passes on the registration call to blpwtk2::ProcessHostImpl,
    // which adds an interface to the registry.
    d_renderProcessHost->Init();

    // It's very important for this constructor to do as little work as
    // possible.  The initialization of RenderProcess is blocked on this
    // function.  Any work that can be lazily executed should be done in
    // in the ProcessHostImpl::bindProcess() function.
}

ProcessHostImpl::Impl::~Impl()
{
    releaseBrowserContext(std::move(d_context));

    if (d_processHandle &&
        d_processHandle != base::GetCurrentProcessHandle()) {
        ::CloseHandle(d_processHandle);
        d_processHandle = 0;
    }
}

// ACCESSORS
inline
base::ProcessId ProcessHostImpl::Impl::processId() const
{
    return d_processId;
}

inline
base::ProcessHandle ProcessHostImpl::Impl::processHandle() const
{
    return d_processHandle;
}

inline
const BrowserContextImpl& ProcessHostImpl::Impl::context() const
{
    return *d_context;
}

inline
const content::RenderProcessHost& ProcessHostImpl::Impl::renderProcessHost() const
{
    return *d_renderProcessHost;
}

// MANIPULATORS
inline
base::ProcessId& ProcessHostImpl::Impl::processId()
{
    return d_processId;
}

inline
base::ProcessHandle& ProcessHostImpl::Impl::processHandle()
{
    return d_processHandle;
}

inline
BrowserContextImpl& ProcessHostImpl::Impl::context()
{
    return *d_context;
}

inline
content::RenderProcessHost& ProcessHostImpl::Impl::renderProcessHost()
{
    return *d_renderProcessHost;
}

                        // ---------------------
                        // class ProcessHostImpl
                        // ---------------------

std::map<base::ProcessId,scoped_refptr<ProcessHostImpl::Impl>> ProcessHostImpl::s_unboundHosts;
static std::set<ProcessHostImpl*> g_instances;

ProcessHostImpl::ProcessHostImpl(const scoped_refptr<base::SingleThreadTaskRunner>& runner)
    : d_runner(runner)
{
    g_instances.insert(this);

    // This constructor mustn't do anything else.  All initializations should
    // happen in the constructor of the Impl class.
}

ProcessHostImpl::~ProcessHostImpl()
{
    g_instances.erase(this);
}

int ProcessHostImpl::createPipeHandleForChild(
        base::ProcessId    processId,
        bool               isolated,
        const std::string& profileDir)
{
    base::ProcessHandle processHandle;

    DCHECK(!d_impl);
    d_impl = new Impl(isolated, profileDir);

    // Create a pipe for Mojo
    mojo::edk::PlatformChannelPair channel_pair;
    HANDLE fileDescriptor;

    if (processId != base::GetCurrentProcId()) {
        processHandle = OpenProcess(PROCESS_DUP_HANDLE, FALSE, processId);

        // Duplicate the "client" side of the pipe on the child process'
        // descriptor table
        BOOL rc = ::DuplicateHandle(
                GetCurrentProcess(),
                channel_pair.PassClientHandle().get().handle,
                processHandle,
                &fileDescriptor,
                0,
                FALSE,
                DUPLICATE_SAME_ACCESS);

        PCHECK(rc != 0);

        // Let the ProcessHostImpl::Impl hold the process handle so it can
        // close it upon object destruction.
        d_impl->processHandle() = processHandle;
    }
    else {
        processHandle = base::GetCurrentProcessHandle();
        fileDescriptor = channel_pair.PassClientHandle().release().handle;
    }

    // Tell Mojo to associate the RenderProcess child token with the "server"
    // side of the pipe.
    mojo::edk::ChildProcessLaunched(
            processHandle,
            std::move(channel_pair.PassServerHandle()),
            d_impl->renderProcessHost().GetChildToken());

    return HandleToLong(fileDescriptor);
}

// static
std::string ProcessHostImpl::createHostChannel(
        base::ProcessId                                    processId,
        bool                                               isolated,
        const std::string&                                 profileDir,
        const scoped_refptr<base::SingleThreadTaskRunner>& runner)
{
    DCHECK(Statics::isInBrowserMainThread());

    ProcessHostImpl processHost(runner);
    ChannelInfo channelInfo;

    // Make sure a host for the same process wasn't already created.
    DCHECK(s_unboundHosts.end() == s_unboundHosts.find(processId));

    // Import the Mojo handle for the "child" process.
    channelInfo.setMojoControllerHandle(
            processHost.createPipeHandleForChild(processId,
                                                 isolated,
                                                 profileDir));

    // Import the renderer command line switches from the render process host.
    base::CommandLine commandLine(base::CommandLine::NO_PROGRAM);
    processHost.d_impl->renderProcessHost().AdjustCommandLineForRenderer(&commandLine);
    channelInfo.loadSwitchesFromCommandLine(commandLine);

    // Stash the host state in the global map
    s_unboundHosts[processId] = processHost.d_impl;

    return channelInfo.serialize();
}

// static
void ProcessHostImpl::create(
        const scoped_refptr<base::SingleThreadTaskRunner>& runner,
        mojo::InterfaceRequest<mojom::ProcessHost>         request)
{
    std::unique_ptr<ProcessHostImpl> processHost(new ProcessHostImpl(runner));
    mojo::MakeStrongBinding(std::move(processHost), std::move(request));
}

// static
void ProcessHostImpl::registerMojoInterfaces(
            service_manager::InterfaceRegistry *registry)
{
    const scoped_refptr<base::SingleThreadTaskRunner>& runner =
            content::BrowserThread::GetTaskRunnerForThread(content::BrowserThread::UI);

    registry->AddInterface(base::Bind(&ProcessHostImpl::create, runner),
                           runner);
}

// static
void ProcessHostImpl::getHostId(int                 *hostId,
                                BrowserContextImpl **context,
                                base::ProcessId      processId)
{
    *hostId = 0;
    *context = 0;

    if (processId != 0) {
        // The requester specified the process id. Let's iterate through all
        // instances of ProcessHost and find the one with the same process id.
        for (const auto& instance : g_instances) {
            if (instance->d_impl && instance->d_impl->processId() == processId) {
                // found!
                *hostId = instance->d_impl->renderProcessHost().GetID();
                *context = &instance->d_impl->context();
                break;
            }
        }
    }
    else {
        // The requester specified a process id of 0, which indicates that the
        // host should spawn a new subprocess and use it for the RenderProcess
        *hostId = content::RenderProcessHostImpl::GenerateUniqueId();
        *context = nullptr;
    }
}

// static
void ProcessHostImpl::releaseAll()
{
    for (auto instance : g_instances) {
        instance->d_impl = nullptr;
    }

    s_unboundHosts.clear();
}

// mojom::ProcessHost overrides
void ProcessHostImpl::createHostChannel(
        unsigned int                     pid,
        bool                             isolated,
        const std::string&               profileDir,
        const createHostChannelCallback& callback)
{
    const scoped_refptr<base::SingleThreadTaskRunner>& runner =
        content::BrowserThread::GetTaskRunnerForThread(content::BrowserThread::UI);

    std::move(callback).Run(createHostChannel(
            static_cast<base::ProcessId>(pid),
            isolated,
            profileDir,
            runner));
}

void ProcessHostImpl::bindProcess(unsigned int pid, bool launchDevToolsServer)
{
    auto it = s_unboundHosts.find(static_cast<base::ProcessId>(pid));
    DCHECK(s_unboundHosts.end() != it);

    if (s_unboundHosts.end() != it) {
        // Move the 'impl' object from the s_unboundHosts map into 'this'.
        DCHECK(!d_impl);
        d_impl = it->second;
        s_unboundHosts.erase(it);

        // Assign the process id to the 'impl' object.
        d_impl->processId() = pid;

        // Launch the DevTools server, if necessary
        if (launchDevToolsServer) {
            d_impl->context().launchDevToolsServerIfNecessary();
        }

        LOG(INFO) << "Bound process host for pid: " << pid;
    }
    else {
        for (const auto& instance : g_instances) {
            if (instance && instance->d_impl && instance->d_impl->processId() == static_cast<base::ProcessId>(pid)) {
                // found!
                d_impl = instance->d_impl;
                LOG(INFO) << "Rebound process host for pid: " << pid;
                break;
            }
        }

        if (!d_impl) {
            // not found!
            LOG(ERROR) << "Couldn't locate process host for pid: " << pid;
        }
    }
}

void ProcessHostImpl::createWebView(
        mojom::WebViewHostRequest     hostRequest,
        mojom::WebViewCreateParamsPtr params,
        bool                          rendererUI,
        const createWebViewCallback&  callback)
{
    int hostId;
    BrowserContextImpl *browserContext;
    getHostId(&hostId,
              &browserContext,
              static_cast<base::ProcessId>(params->processId));

    if (hostId) {
        if (nullptr == browserContext) {
            // If getHostId() wasn't able to find a browser context (probably
            // because processId didn't match any of the ProcessHost
            // instances), we fallback to using the browser context that is
            // associated with the current ProcessHost.  Since the lifetime of
            // the newly created webview and that of the current process host
            // is bounded by the requester's lifetime, they'll both go away
            // together when the requester disappears.
            browserContext = &d_impl->context();
        }

        auto taskRunner =
            content::BrowserThread::GetTaskRunnerForThread(content::BrowserThread::UI);

        mojom::WebViewClientPtr clientPtr;
        std::move(callback).Run(mojo::MakeRequest(&clientPtr, taskRunner), 0);

        // Create an instance of WebViewHost and bind its lifetime to hostRequest.
        // We are passing a Mojo interface pointer to the renderer's toolkit as
        // well as a new instance of WebViewImpl to the WebViewHost.
        mojo::MakeStrongBinding(
                std::make_unique<WebViewHostImpl>(std::move(clientPtr),
                                                  *params,
                                                  rendererUI,
                                                  browserContext,
                                                  hostId,
                                                  d_impl),
                std::move(hostRequest));
    }
    else {
        mojom::WebViewClientPtr clientPtr;
        std::move(callback).Run(mojo::MakeRequest(&clientPtr), ESRCH);
    }
}

void ProcessHostImpl::registerNativeViewForStreaming(
    unsigned int view, const registerNativeViewForStreamingCallback& callback)
{
    String media_id = d_impl->context().registerNativeViewForStreaming(
            reinterpret_cast<NativeView>(view));
    std::move(callback).Run(std::string(media_id.data(), media_id.size()));
}

void ProcessHostImpl::registerScreenForStreaming(
    unsigned int screen, const registerScreenForStreamingCallback& callback)
{
    String media_id = d_impl->context().registerScreenForStreaming(
            reinterpret_cast<NativeScreen>(screen));
    std::move(callback).Run(std::string(media_id.data(), media_id.size()));
}

void ProcessHostImpl::setDefaultPrinter(const std::string& name)
{
    d_impl->context().setDefaultPrinter(StringRef(name));
}

void ProcessHostImpl::dumpDiagnostics(int type, const std::string& path)
{
    d_impl->context().dumpDiagnostics(
            static_cast<Profile::DiagnosticInfoType>(type), StringRef(path));
}

void ProcessHostImpl::addHttpProxy(mojom::ProxyConfigType type,
                                   const std::string&     host,
                                   int                    port)
{
    d_impl->context().addHttpProxy(static_cast<ProxyType>(type),
                                   StringRef(host),
                                   port);
}

void ProcessHostImpl::addHttpsProxy(mojom::ProxyConfigType type,
                                    const std::string&     host,
                                    int                    port)
{
    d_impl->context().addHttpsProxy(static_cast<ProxyType>(type),
                                    StringRef(host),
                                    port);
}

void ProcessHostImpl::addFtpProxy(mojom::ProxyConfigType type,
                                  const std::string&     host,
                                  int                    port)
{
    d_impl->context().addFtpProxy(static_cast<ProxyType>(type),
                                   StringRef(host),
                                   port);
}

void ProcessHostImpl::addFallbackProxy(mojom::ProxyConfigType type,
                                       const std::string&     host,
                                       int                    port)
{
    d_impl->context().addFallbackProxy(static_cast<ProxyType>(type),
                                       StringRef(host),
                                       port);
}

void ProcessHostImpl::clearHttpProxies()
{
    d_impl->context().clearHttpProxies();
}

void ProcessHostImpl::clearHttpsProxies()
{
    d_impl->context().clearHttpsProxies();
}

void ProcessHostImpl::clearFtpProxies()
{
    d_impl->context().clearFtpProxies();
}

void ProcessHostImpl::clearFallbackProxies()
{
    d_impl->context().clearFallbackProxies();
}

void ProcessHostImpl::addBypassRule(const std::string& rule)
{
    d_impl->context().addBypassRule(StringRef(rule));
}

void ProcessHostImpl::clearBypassRules()
{
    d_impl->context().clearBypassRules();
}

void ProcessHostImpl::clearWebCache()
{
    d_impl->context().clearWebCache();
}

void ProcessHostImpl::setPacUrl(const std::string& url)
{
    d_impl->context().setPacUrl(StringRef(url));
}

void ProcessHostImpl::enableSpellCheck(bool enabled)
{
    d_impl->context().enableSpellCheck(enabled);
}

void ProcessHostImpl::setLanguages(const std::vector<std::string>& languages)
{
    std::vector<StringRef> languageList;

    for (auto& lang : languages) {
        languageList.push_back(StringRef(lang));
    }

    d_impl->context().setLanguages(languageList.data(), languageList.size());
}

void ProcessHostImpl::addCustomWords(const std::vector<std::string>& words)
{
    std::vector<StringRef> wordList;

    for (auto& word : words) {
        wordList.push_back(StringRef(word));
    }

    d_impl->context().addCustomWords(wordList.data(), wordList.size());
}

void ProcessHostImpl::removeCustomWords(const std::vector<std::string>& words)
{
    std::vector<StringRef> wordList;

    for (auto& word : words) {
        wordList.push_back(StringRef(word));
    }

    d_impl->context().removeCustomWords(wordList.data(), wordList.size());
}

}  // close namespace blpwtk2

// vim: ts=4 et

