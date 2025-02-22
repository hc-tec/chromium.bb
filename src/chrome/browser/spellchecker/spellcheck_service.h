// Copyright (c) 2012 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef CHROME_BROWSER_SPELLCHECKER_SPELLCHECK_SERVICE_H_
#define CHROME_BROWSER_SPELLCHECKER_SPELLCHECK_SERVICE_H_

#include <stddef.h>

#include <memory>
#include <string>
#include <vector>

#include "base/gtest_prod_util.h"
#include "base/macros.h"
#include "base/memory/scoped_vector.h"
#include "base/memory/weak_ptr.h"
#include "build/build_config.h"

// SHEZ: Remove dependency on Chrome's custom dictionary
#if 0
#include "chrome/browser/spellchecker/spellcheck_custom_dictionary.h"
#endif

#include "chrome/browser/spellchecker/spellcheck_hunspell_dictionary.h"
#include "components/keyed_service/core/keyed_service.h"
#include "components/prefs/pref_change_registrar.h"
#include "content/public/browser/notification_observer.h"
#include "content/public/browser/notification_registrar.h"
#include "content/public/browser/spellcheck_data.h"

class SpellCheckHostMetrics;

namespace base {
class WaitableEvent;
class SupportsUserData;
}

namespace content {
class RenderProcessHost;
class BrowserContext;
class NotificationDetails;
class NotificationSource;
}

namespace spellcheck {
// SHEZ: Remove feedback sender
// class FeedbackSender;
}

// Encapsulates the browser side spellcheck service. There is one of these per
// profile and each is created by the SpellCheckServiceFactory.  The
// SpellcheckService maintains any per-profile information about spellcheck.
class SpellcheckService : public KeyedService,
                          public content::NotificationObserver,
                          public content::SpellcheckData::Observer,
                          // SHEZ: Remove dependency on Chrome's custom dictionary
#if 0
                          public SpellcheckCustomDictionary::Observer,
#endif
                          public SpellcheckHunspellDictionary::Observer {
 public:
  // Event types used for reporting the status of this class and its derived
  // classes to browser tests.
  enum EventType {
    BDICT_NOTINITIALIZED,
    BDICT_CORRUPTED,
  };

  // Dictionary format used for loading an external dictionary.
  enum DictionaryFormat {
    DICT_HUNSPELL,
    DICT_TEXT,
    DICT_UNKNOWN,
  };

  // A dictionary that can be used for spellcheck.
  struct Dictionary {
    // The shortest unambiguous identifier for a language from
    // |g_supported_spellchecker_languages|. For example, "bg" for Bulgarian,
    // because Chrome has only one Bulgarian language. However, "en-US" for
    // English (United States), because Chrome has several versions of English.
    std::string language;

    // Whether |language| is currently used for spellcheck.
    bool used_for_spellcheck;
  };

  explicit SpellcheckService(content::BrowserContext* context);
  ~SpellcheckService() override;

  base::WeakPtr<SpellcheckService> GetWeakPtr();

#if !defined(OS_MACOSX)
  // Returns all currently configured |dictionaries| to display in the context
  // menu over a text area. The context menu is used for selecting the
  // dictionaries used for spellcheck.
  static void GetDictionaries(base::SupportsUserData* browser_context,
                              std::vector<Dictionary>* dictionaries);
#endif  // !OS_MACOSX

  // Signals the event attached by AttachTestEvent() to report the specified
  // event to browser tests. This function is called by this class and its
  // derived classes to report their status. This function does not do anything
  // when we do not set an event to |status_event_|.
  static bool SignalStatusEvent(EventType type);

  // Instantiates SpellCheckHostMetrics object and makes it ready for recording
  // metrics. This should be called only if the metrics recording is active.
  void StartRecordingMetrics(bool spellcheck_enabled);

  // Pass the renderer some basic initialization information. Note that the
  // renderer will not load Hunspell until it needs to.
  void InitForRenderer(content::RenderProcessHost* process);

  // Returns a metrics counter associated with this object,
  // or null when metrics recording is disabled.
  SpellCheckHostMetrics* GetMetrics() const;

// SHEZ: Remove dependency on Chrome's custom dictionary
#if 0
  // Returns the instance of the custom dictionary.
  SpellcheckCustomDictionary* GetCustomDictionary();
#endif

  // Starts the process of loading the Hunspell dictionaries.
  void LoadHunspellDictionaries();

  // Returns the instance of the vector of Hunspell dictionaries.
  const ScopedVector<SpellcheckHunspellDictionary>& GetHunspellDictionaries();

  // Returns the instance of the spelling service feedback sender.
  // SHEZ: Remove feedback sender
  // spellcheck::FeedbackSender* GetFeedbackSender();

  // Load a dictionary from a given path. Format specifies how the dictionary
  // is stored. Return value is true if successful.
  bool LoadExternalDictionary(std::string language,
                              std::string locale,
                              std::string path,
                              DictionaryFormat format);

  // Unload a dictionary. The path is given to identify the dictionary.
  // Return value is true if successful.
  bool UnloadExternalDictionary(const std::string& /* path */);

  // NotificationProfile implementation.
  void Observe(int type,
               const content::NotificationSource& source,
               const content::NotificationDetails& details) override;

  // content::SpellcheckData::Observer implementation.
  void OnCustomWordsChanged(
      const std::vector<base::StringPiece>& words_added,
      const std::vector<base::StringPiece>& words_removed) override;

// SHEZ: Remove dependency on Chrome's custom dictionary
#if 0
  // SpellcheckCustomDictionary::Observer implementation.
  void OnCustomDictionaryLoaded() override;
  void OnCustomDictionaryChanged(
      const SpellcheckCustomDictionary::Change& dictionary_change) override;
#endif

  // SpellcheckHunspellDictionary::Observer implementation.
  void OnHunspellDictionaryInitialized(const std::string& language) override;
  void OnHunspellDictionaryDownloadBegin(const std::string& language) override;
  void OnHunspellDictionaryDownloadSuccess(
      const std::string& language) override;
  void OnHunspellDictionaryDownloadFailure(
      const std::string& language) override;

 private:
  FRIEND_TEST_ALL_PREFIXES(SpellcheckServiceBrowserTest, DeleteCorruptedBDICT);

  // Attaches an event so browser tests can listen the status events.
  static void AttachStatusEvent(base::WaitableEvent* status_event);

  // Returns the status event type.
  static EventType GetStatusEvent();

  // Pass all renderers some basic initialization information.
  void InitForAllRenderers();

  // Reacts to a change in user preference on which languages should be used for
  // spellchecking.
  void OnSpellCheckDictionariesChanged();

  // Notification handler for changes to prefs::kSpellCheckUseSpellingService.
  void OnUseSpellingServiceChanged();

  // Notification handler for changes to prefs::kAcceptLanguages. Removes from
  // prefs::kSpellCheckDictionaries any languages that are not in
  // prefs::kAcceptLanguages.
  void OnAcceptLanguagesChanged();

  // Enables the feedback sender if spelling server is available and enabled.
  // Otherwise disables the feedback sender.
  // SHEZ: Remove feedback sender
  // void UpdateFeedbackSenderState();

  PrefChangeRegistrar pref_change_registrar_;
  content::NotificationRegistrar registrar_;

  // A pointer to the BrowserContext which this service refers to.
  content::BrowserContext* context_;

  std::unique_ptr<SpellCheckHostMetrics> metrics_;

  ScopedVector<SpellcheckHunspellDictionary> hunspell_dictionaries_;

  base::WeakPtrFactory<SpellcheckService> weak_ptr_factory_;

  DISALLOW_COPY_AND_ASSIGN(SpellcheckService);
};

#endif  // CHROME_BROWSER_SPELLCHECKER_SPELLCHECK_SERVICE_H_
