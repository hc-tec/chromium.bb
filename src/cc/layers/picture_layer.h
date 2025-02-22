// Copyright 2012 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef CC_LAYERS_PICTURE_LAYER_H_
#define CC_LAYERS_PICTURE_LAYER_H_

#include "base/macros.h"
#include "cc/base/invalidation_region.h"
#include "cc/debug/devtools_instrumentation.h"
#include "cc/debug/micro_benchmark_controller.h"
#include "cc/layers/layer.h"

namespace cc {

class ContentLayerClient;
class DisplayItemList;
class RecordingSource;

class CC_EXPORT PictureLayer : public Layer {
 public:
  static scoped_refptr<PictureLayer> Create(ContentLayerClient* client);

  void ClearClient();

  void SetNearestNeighbor(bool nearest_neighbor);
  bool nearest_neighbor() const {
    return picture_layer_inputs_.nearest_neighbor;
  }

  void SetDefaultLCDBackgroundColor(SkColor default_lcd_background_color);

  // Layer interface.
  std::unique_ptr<LayerImpl> CreateLayerImpl(LayerTreeImpl* tree_impl) override;
  void SetLayerTreeHost(LayerTreeHost* host) override;
  void PushPropertiesTo(LayerImpl* layer) override;
  void SetNeedsDisplayRect(const gfx::Rect& layer_rect) override;
  bool Update() override;
  void SetIsMask(bool is_mask) override;
  sk_sp<SkPicture> GetPicture() const override;

  void SetTypeForProtoSerialization(proto::LayerNode* proto) const override;
  void ToLayerPropertiesProto(proto::LayerProperties* proto) override;

  bool IsSuitableForGpuRasterization() const override;

  void RunMicroBenchmark(MicroBenchmark* benchmark) override;

  ContentLayerClient* client() { return picture_layer_inputs_.client; }

  RecordingSource* GetRecordingSourceForTesting() {
    return recording_source_.get();
  }

  const DisplayItemList* GetDisplayItemList();

 protected:
  // Encapsulates all data, callbacks or interfaces received from the embedder.
  struct PictureLayerInputs {
    PictureLayerInputs();
    ~PictureLayerInputs();

    ContentLayerClient* client = nullptr;
    bool nearest_neighbor = false;
    gfx::Rect recorded_viewport;
    scoped_refptr<DisplayItemList> display_list;
    size_t painter_reported_memory_usage = 0;
    SkColor default_lcd_background_color = SK_ColorTRANSPARENT;
  };

  explicit PictureLayer(ContentLayerClient* client);
  // Allow tests to inject a recording source.
  PictureLayer(ContentLayerClient* client,
               std::unique_ptr<RecordingSource> source);
  ~PictureLayer() override;

  bool HasDrawableContent() const override;

  bool is_mask() const { return is_mask_; }

  PictureLayerInputs picture_layer_inputs_;

 private:
  friend class TestSerializationPictureLayer;

  void DropRecordingSourceContentIfInvalid();

  bool ShouldUseTransformedRasterization() const;

  std::unique_ptr<RecordingSource> recording_source_;
  devtools_instrumentation::
      ScopedLayerObjectTracker instrumentation_object_tracker_;

  Region last_updated_invalidation_;

  int update_source_frame_number_;
  bool is_mask_;

  DISALLOW_COPY_AND_ASSIGN(PictureLayer);
};

}  // namespace cc

#endif  // CC_LAYERS_PICTURE_LAYER_H_
