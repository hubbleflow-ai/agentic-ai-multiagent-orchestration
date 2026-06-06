import { configureStore } from "@reduxjs/toolkit";

import { uiReducer } from "./uiSlice";
import { agentReducer } from "./agentSlice";
import { chatReducer } from "./chatSlice";
import { tripReducer } from "./tripSlice";
import { voiceReducer } from "./voiceSlice";

export const store = configureStore({
  reducer: {
    ui: uiReducer,
    agent: agentReducer,
    chat: chatReducer,
    trip: tripReducer,
    voice: voiceReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
