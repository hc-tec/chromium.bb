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

#ifndef INCLUDED_BLPWTK2_PROCESSHOSTIMPL_H
#define INCLUDED_BLPWTK2_PROCESSHOSTIMPL_H

#include <blpwtk2_config.h>
#include <blpwtk2/private/blpwtk2_process.mojom.h>

#include <base/compiler_specific.h>
#include <base/id_map.h>
#include <base/process/process_handle.h>
#include <base/memory/ref_counted.h>
#include <ipc/ipc_listener.h>
#include <third_party/WebKit/public/platform/InterfaceRegistry.h>
#include <services/service_manager/public/cpp/interface_registry.h>
#include <content/public/browser/render_process_host.h>

#include <string>

namespace blpwtk2 {
class ProcessHostImplInternal;
class BrowserContextImpl;

                        // =====================
                        // class ProcessHostImpl
                        // =====================

class ProcessHostImpl final : public mojom::ProcessHost
{
  public:
    // TYPE
    class Impl;

  private:
    // FRIENDS
    friend std::unique_ptr<ProcessHostImpl>::deleter_type;

    // DATA
    scoped_refptr<Impl> d_impl;
    scoped_refptr<base::SingleThreadTaskRunner> d_runner;
    static std::map<base::ProcessId,scoped_refptr<Impl> > s_unboundHosts;

    explicit ProcessHostImpl(const scoped_refptr<base::SingleThreadTaskRunner>& runner);
        // Private constructor.  This object should only be created by
        // createHostChannel() or create().

    ~ProcessHostImpl();
        // Private destructor.

    int createPipeHandleForChild(base::ProcessId    processId,
                                 bool               isolated,
                                 const std::string& profileDir);
        // Creates a two-way pipe between process 'processId' and the current
        // process.  Returns the file descriptor of the client side of
        // the pipe pair.  Note that the returned value is with respect
        // to the file descriptor table of process 'processId'.

    DISALLOW_COPY_AND_ASSIGN(ProcessHostImpl);

  public:
    static std::string createHostChannel(
            base::ProcessId                                    processId,
            bool                                               isolated,
            const std::string&                                 profileDir,
            const scoped_refptr<base::SingleThreadTaskRunner>& runner);
        // Create a bootstrap process host.  This allows the browser process
        // to preemptively create an instance of process host, which
        // transitively creates a message pipe between process 'pid' and the
        // current process.  This opens up a way for the process 'pid' to send
        // Mojo requests to this process.  The ownership of this bootstrap
        // instance will be transferred to Mojo on the very next Mojo service
        // request, at which point all process hosts (including the ones
        // created later) will be owned by Mojo.

    static void create(
            const scoped_refptr<base::SingleThreadTaskRunner>& runner,
            mojo::InterfaceRequest<mojom::ProcessHost>         request);
        // Create an instance of process host in response to a Mojo service
        // call.

    static void registerMojoInterfaces(
            service_manager::InterfaceRegistry *registry);
        // Registers this class to the Mojo registry.  This allows Mojo to
        // create instances of this class (by calling create()) when a new
        // service request comes in.

    static void getHostId(int                 *hostId,
                          BrowserContextImpl **context,
                          base::ProcessId      processId);

    static void releaseAll();

    // mojom::ProcessHost overrides
    void createHostChannel(
            unsigned int                     pid,
            bool                             isolated,
            const std::string&               profileDir,
            const createHostChannelCallback& callback) override;
    void bindProcess(unsigned int pid, bool launchDevToolsServer) override;

    void createWebView(
            mojom::WebViewHostRequest     hostRequest,
            mojom::WebViewCreateParamsPtr params,
            bool                          rendererUI,
            const createWebViewCallback&  callback) override;
    void registerNativeViewForStreaming(
            unsigned int                                  view,
            const registerNativeViewForStreamingCallback& callback) override;

    void registerScreenForStreaming(
            unsigned int                              screen,
            const registerScreenForStreamingCallback& callback) override;

    void setDefaultPrinter(const std::string& name) override;

    void dumpDiagnostics(int type, const std::string& path) override;

    void addHttpProxy(mojom::ProxyConfigType type,
                      const std::string&     host,
                      int                    port) override;
    void addHttpsProxy(mojom::ProxyConfigType type,
                       const std::string&     host,
                       int                    port) override;
    void addFtpProxy(mojom::ProxyConfigType type,
                     const std::string&     host,
                     int                    port) override;
    void addFallbackProxy(mojom::ProxyConfigType type,
                          const std::string&     host,
                          int                    port) override;
    void clearHttpProxies() override;
    void clearHttpsProxies() override;
    void clearFtpProxies() override;
    void clearFallbackProxies() override;
    void addBypassRule(const std::string& rule) override;
    void clearBypassRules() override;
    void clearWebCache() override;
    void setPacUrl(const std::string& url) override;
    void enableSpellCheck(bool enabled) override;
    void setLanguages(const std::vector<std::string>& languages) override;
    void addCustomWords(const std::vector<std::string>& words) override;
    void removeCustomWords(const std::vector<std::string>& words) override;
};

                        // ===========================
                        // class ProcessHostImpl::Impl
                        // ===========================

class ProcessHostImpl::Impl final : public base::RefCounted<Impl>
{
    // DATA
    base::ProcessId d_processId;
    scoped_refptr<BrowserContextImpl> d_context;
    std::unique_ptr<content::RenderProcessHost> d_renderProcessHost;
    base::ProcessHandle d_processHandle;

    friend class base::RefCounted<Impl>;
    ~Impl();

  public:
    // CREATORS
    Impl(bool isolated, const std::string& profileDir);
        // Initialize the ProcessHost (the real implementation).
        // 'profileDir' is a path to the directory that will be used by the
        // browser context to store profile data.
        // 'launchDevToolsServer' determines if the DevTools server should be
        // launched.  This is required for a DevTools frontend to connect to
        // the backend.  This should *NEVER* be set to true in untrusted
        // environments.

    // ACCESSORS
    base::ProcessId processId() const;
    base::ProcessHandle processHandle() const;
    const BrowserContextImpl& context() const;
    const content::RenderProcessHost& renderProcessHost() const;

    // MANIPULATORS
    base::ProcessId& processId();
    base::ProcessHandle& processHandle();
    BrowserContextImpl& context();
    content::RenderProcessHost& renderProcessHost();
};



}  // close namespace blpwtk2

#endif  // INCLUDED_BLPWTK2_PROCESSHOSTIMPL_H

// vim: ts=4 et

