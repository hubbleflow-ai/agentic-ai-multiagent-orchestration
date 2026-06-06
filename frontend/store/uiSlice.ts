import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

/**
 * Cross-cutting UI state · ported from S5 with voice fields added.
 *
 *  - theme         light | dark, persisted to localStorage
 *  - panel         the right-side trip plan drawer
 *      open            visible
 *      pinned          stay open across runs
 *      userClosedRun   user closed during this run; suppress auto-reveal
 *      pendingPulse    since-last-open changes (drives peek-tab dot)
 *  - voice         mic state for the Concierge voice path
 *      listening       Web Audio capture active
 *      speaking        Live currently outputting audio (drives waveform)
 */

export type Theme = "light" | "dark";

type UiSliceState = {
  theme: Theme;
  panel: {
    open: boolean;
    pinned: boolean;
    userClosedRun: boolean;
    pendingPulse: number;
  };
  voice: {
    listening: boolean;
    speaking: boolean;
  };
};

const initialState: UiSliceState = {
  theme: "light",
  panel: {
    open: false,
    pinned: false,
    userClosedRun: false,
    pendingPulse: 0,
  },
  voice: {
    listening: false,
    speaking: false,
  },
};

const uiSlice = createSlice({
  name: "ui",
  initialState,
  reducers: {
    setTheme(state, action: PayloadAction<Theme>) {
      state.theme = action.payload;
    },
    toggleTheme(state) {
      state.theme = state.theme === "light" ? "dark" : "light";
    },

    openPanel(state) {
      state.panel.open = true;
      state.panel.userClosedRun = false;
      state.panel.pendingPulse = 0;
    },
    closePanel(state) {
      if (state.panel.pinned) return;
      state.panel.open = false;
      state.panel.userClosedRun = true;
    },
    togglePinned(state) {
      state.panel.pinned = !state.panel.pinned;
      if (state.panel.pinned) {
        state.panel.open = true;
        state.panel.userClosedRun = false;
      }
    },
    notifyPanelUpdate(state) {
      if (state.panel.userClosedRun && !state.panel.pinned) {
        state.panel.pendingPulse += 1;
      } else {
        state.panel.open = true;
        state.panel.pendingPulse = 0;
      }
    },
    resetPanelForNewRun(state) {
      state.panel.userClosedRun = false;
    },

    setListening(state, action: PayloadAction<boolean>) {
      state.voice.listening = action.payload;
    },
    setSpeaking(state, action: PayloadAction<boolean>) {
      state.voice.speaking = action.payload;
    },
  },
});

export const {
  setTheme,
  toggleTheme,
  openPanel,
  closePanel,
  togglePinned,
  notifyPanelUpdate,
  resetPanelForNewRun,
  setListening,
  setSpeaking,
} = uiSlice.actions;

export const uiReducer = uiSlice.reducer;
