"use client";

import { Provider } from "react-redux";
import { store } from "@/store";
import { VoicePlaybackController } from "@/components/VoicePlaybackController";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <Provider store={store}>
      {/* App-shell hook that watches chat.turns and speaks new assistant
          turns via SpeechSynthesis when voice mode is on. Renders nothing. */}
      <VoicePlaybackController />
      {children}
    </Provider>
  );
}
