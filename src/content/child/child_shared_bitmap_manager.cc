// Copyright (c) 2013 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "content/child/child_shared_bitmap_manager.h"

#include <stddef.h>

#include <utility>

#include "base/debug/alias.h"
#include "base/memory/ptr_util.h"
#include "base/process/memory.h"
#include "base/process/process_metrics.h"
#include "build/build_config.h"
#include "content/child/child_thread_impl.h"
#include "content/common/child_process_messages.h"
#include "ui/gfx/geometry/size.h"

namespace content {

namespace {

class ChildSharedBitmap : public SharedMemoryBitmap {
 public:
  ChildSharedBitmap(scoped_refptr<ThreadSafeSender> sender,
                    base::SharedMemory* shared_memory,
                    const cc::SharedBitmapId& id,
                    ChildSharedBitmapManager* manager)
      : SharedMemoryBitmap(static_cast<uint8_t*>(shared_memory->memory()),
                           id,
                           shared_memory),
        sender_(sender),
        manager_(manager) {}

  ChildSharedBitmap(scoped_refptr<ThreadSafeSender> sender,
                    std::unique_ptr<base::SharedMemory> shared_memory_holder,
                    const cc::SharedBitmapId& id,
                    ChildSharedBitmapManager* manager)
      : ChildSharedBitmap(sender, shared_memory_holder.get(), id, manager) {
    shared_memory_holder_ = std::move(shared_memory_holder);
  }

  ~ChildSharedBitmap() override {
    if (manager_)
      manager_->FreeSharedMemoryFromMap(id());
    sender_->Send(new ChildProcessHostMsg_DeletedSharedBitmap(id()));
  }

 private:
  scoped_refptr<ThreadSafeSender> sender_;
  std::unique_ptr<base::SharedMemory> shared_memory_holder_;
  ChildSharedBitmapManager* manager_;
};

class WeakChildSharedBitmap : public SharedMemoryBitmap {
 public:
  WeakChildSharedBitmap(base::SharedMemory* shared_memory,
                        const cc::SharedBitmapId& id)
      : SharedMemoryBitmap(static_cast<uint8_t*>(shared_memory->memory()),
                           id,
                           shared_memory) {
  }

  ~WeakChildSharedBitmap() override {
  }
};

// Collect extra information for debugging bitmap creation failures.
void CollectMemoryUsageAndDie(const gfx::Size& size, size_t alloc_size) {
#if defined(OS_WIN)
  int width = size.width();
  int height = size.height();
  DWORD last_error = GetLastError();

  std::unique_ptr<base::ProcessMetrics> metrics(
      base::ProcessMetrics::CreateProcessMetrics(
          base::GetCurrentProcessHandle()));

  size_t private_bytes = 0;
  size_t shared_bytes = 0;
  metrics->GetMemoryBytes(&private_bytes, &shared_bytes);

  base::debug::Alias(&width);
  base::debug::Alias(&height);
  base::debug::Alias(&last_error);
  base::debug::Alias(&private_bytes);
  base::debug::Alias(&shared_bytes);
#endif
  base::TerminateBecauseOutOfMemory(alloc_size);
}

}  // namespace

SharedMemoryBitmap::SharedMemoryBitmap(uint8_t* pixels,
                                       const cc::SharedBitmapId& id,
                                       base::SharedMemory* shared_memory)
    : SharedBitmap(pixels, id), shared_memory_(shared_memory) {}

ChildSharedBitmapManager::ChildSharedBitmapManager(
    scoped_refptr<ThreadSafeSender> sender)
    : sender_(sender) {
}

ChildSharedBitmapManager::~ChildSharedBitmapManager() {
  DCHECK(shared_memory_map_.empty());
}

std::unique_ptr<cc::SharedBitmap>
ChildSharedBitmapManager::AllocateSharedBitmap(const gfx::Size& size) {
  std::unique_ptr<SharedMemoryBitmap> bitmap = AllocateSharedMemoryBitmap(size);
#if defined(OS_POSIX)
  // Close file descriptor to avoid running out.
  if (bitmap)
    bitmap->shared_memory()->Close();
#endif
  return std::move(bitmap);
}

std::unique_ptr<SharedMemoryBitmap>
ChildSharedBitmapManager::AllocateSharedMemoryBitmap(const gfx::Size& size) {
  TRACE_EVENT2("renderer",
               "ChildSharedBitmapManager::AllocateSharedMemoryBitmap", "width",
               size.width(), "height", size.height());
  size_t memory_size;
  if (!cc::SharedBitmap::SizeInBytes(size, &memory_size))
    return std::unique_ptr<SharedMemoryBitmap>();
  cc::SharedBitmapId id = cc::SharedBitmap::GenerateId();
  std::unique_ptr<base::SharedMemory> memory;
#if defined(OS_POSIX)
  base::SharedMemoryHandle handle;
  bool send_success =
      sender_->Send(new ChildProcessHostMsg_SyncAllocateSharedBitmap(
          memory_size, id, &handle));
  if (!send_success) {
    // Callers of this method are not prepared to handle failures during
    // shutdown. Exit immediately. This is expected behavior during the Fast
    // Shutdown path, so use EXIT_SUCCESS. https://crbug.com/615121.
    exit(EXIT_SUCCESS);
  }
  memory = base::MakeUnique<base::SharedMemory>(handle, false);
  if (!memory->Map(memory_size))
    CollectMemoryUsageAndDie(size, memory_size);
#else
  bool out_of_memory;
  memory = ChildThreadImpl::AllocateSharedMemory(memory_size, sender_.get(),
                                                 &out_of_memory);
  if (!memory) {
    if (out_of_memory) {
      CollectMemoryUsageAndDie(size, memory_size);
    } else {
      // Callers of this method are not prepared to handle failures during
      // shutdown. Exit immediately. This is expected behavior during the Fast
      // Shutdown path, so use EXIT_SUCCESS. https://crbug.com/615121.
      exit(EXIT_SUCCESS);
    }
  }

  if (!memory->Map(memory_size))
    CollectMemoryUsageAndDie(size, memory_size);

  base::SharedMemoryHandle handle_to_send = memory->handle();
  sender_->Send(new ChildProcessHostMsg_AllocatedSharedBitmap(
      memory_size, handle_to_send, id));
#endif
  shared_memory_map_[id] = memory.get();
  return base::MakeUnique<ChildSharedBitmap>(
    sender_, std::move(memory), id, this);
}

std::unique_ptr<cc::SharedBitmap>
ChildSharedBitmapManager::GetSharedBitmapFromId(const gfx::Size& size,
                                                const cc::SharedBitmapId& id) {
  SharedMemoryMap::iterator it = shared_memory_map_.find(id);
  if (it == shared_memory_map_.end())
    return std::unique_ptr<cc::SharedBitmap>();

  base::SharedMemory* shared_memory = it->second;

  return base::WrapUnique(
      new WeakChildSharedBitmap(shared_memory, id));
}

std::unique_ptr<cc::SharedBitmap>
ChildSharedBitmapManager::GetBitmapForSharedMemory(base::SharedMemory* mem) {
  cc::SharedBitmapId id = cc::SharedBitmap::GenerateId();
  base::SharedMemoryHandle handle_to_send = mem->handle();
#if defined(OS_POSIX)
  if (!mem->ShareToProcess(base::GetCurrentProcessHandle(), &handle_to_send))
    return std::unique_ptr<cc::SharedBitmap>();
#endif
  sender_->Send(new ChildProcessHostMsg_AllocatedSharedBitmap(
      mem->mapped_size(), handle_to_send, id));
  shared_memory_map_[id] = mem;

  return base::MakeUnique<ChildSharedBitmap>(sender_, mem, id, this);
}

void ChildSharedBitmapManager::FreeSharedMemoryFromMap(
    const cc::SharedBitmapId& id) {
  shared_memory_map_.erase(id);
}

}  // namespace content
