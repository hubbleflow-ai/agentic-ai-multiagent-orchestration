import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

/**
 * Voice mode state.
 *
 *  - mode               · "off" (text only) or "on" (mic + TTS active)
 *  - listening          · STT is actively capturing speech
 *  - speaking           · TTS is actively producing audio
 *  - interimTranscript  · live transcript displayed in the textbox while
 *                          the user is speaking; cleared on send
 *  - spokenCharIndex    · charIndex into the current agent message that
 *                          TTS is currently speaking; drives the karaoke
 *                          highlight in the chat
 *  - spokenMessageId    · the assistant turn id whose text is being spoken
 */

export type VoiceMode = "off" | "on";

type VoiceState = {
  mode: VoiceMode;
  listening: boolean;
  speaking: boolean;
  interimTranscript: string;
  spokenCharIndex: number;
  spokenMessageId: string | null;
  error: string | null;
};

const initialState: VoiceState = {
  mode: "off",
  listening: false,
  speaking: false,
  interimTranscript: "",
  spokenCharIndex: 0,
  spokenMessageId: null,
  error: null,
};

const voiceSlice = createSlice({
  name: "voice",
  initialState,
  reducers: {
    setMode(state, action: PayloadAction<VoiceMode>) {
      state.mode = action.payload;
      if (action.payload === "off") {
        state.listening = false;
        state.speaking = false;
        state.interimTranscript = "";
        state.spokenCharIndex = 0;
        state.spokenMessageId = null;
      }
    },
    setListening(state, action: PayloadAction<boolean>) {
      state.listening = action.payload;
      if (!action.payload) state.interimTranscript = "";
    },
    setSpeaking(state, action: PayloadAction<boolean>) {
      state.speaking = action.payload;
      if (!action.payload) {
        state.spokenCharIndex = 0;
        state.spokenMessageId = null;
      }
    },
    setInterimTranscript(state, action: PayloadAction<string>) {
      state.interimTranscript = action.payload;
    },
    setSpokenMessageId(state, action: PayloadAction<string | null>) {
      state.spokenMessageId = action.payload;
      state.spokenCharIndex = 0;
    },
    setSpokenCharIndex(state, action: PayloadAction<number>) {
      state.spokenCharIndex = action.payload;
    },
    setError(state, action: PayloadAction<string | null>) {
      state.error = action.payload;
    },
  },
});

export const {
  setMode,
  setListening,
  setSpeaking,
  setInterimTranscript,
  setSpokenMessageId,
  setSpokenCharIndex,
  setError: setVoiceError,
} = voiceSlice.actions;

export const voiceReducer = voiceSlice.reducer;
