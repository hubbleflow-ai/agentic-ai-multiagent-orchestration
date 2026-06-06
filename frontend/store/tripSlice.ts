import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

/**
 * Trip artifacts · multi-segment / multi-stay capable.
 *
 * A "trip" can be:
 *   - single leg + single stay (Tokyo trip · BLR↔NRT, Park Hyatt)
 *   - multi-leg + multi-stay  (Europe trip · BLR→LHR, LHR→CDG, CDG→FCO, FCO→BLR
 *                              with hotel stays in London, Paris, Rome)
 *
 * Each `search_flights` tool call creates a new FlightSegment keyed by
 * (origin, destination, depart_date). Each `search_hotels` creates a new
 * HotelStay keyed by (city, check_in, check_out). Subsequent results for
 * the same key UPDATE that segment in-place instead of overwriting the
 * whole list.
 */

export type FlightOption = {
  flight_id: string;
  airline: string;
  alliance?: string;
  origin: string;
  destination: string;
  depart_date: string;
  return_date?: string | null;
  dep_time: string;
  arr_time: string;
  duration_hours: number;
  stops: number;
  price_per_pax_inr: number;
  price_total_inr: number;
  pax: number;
};

export type HotelOption = {
  hotel_id: string;
  name: string;
  city: string;
  neighborhood: string;
  rating: number;
  amenities: string[];
  per_night_inr: number;
  nights: number;
  total_inr: number;
  pax: number;
};

export type ItineraryDay = {
  day: number;
  title: string;
  items: { time: string; what: string }[];
  notes?: string[];
};

export type PaymentStatus = "idle" | "authorized" | "captured" | "declined";

export type FlightSegment = {
  key: string;
  origin: string;
  destination: string;
  depart_date: string;
  return_date: string | null;
  options: FlightOption[];
  recommendedId: string | null;
  selected: FlightOption | null;
  holdId: string | null;
  searching: boolean;
};

export type HotelStay = {
  key: string;
  city: string;
  check_in: string;
  check_out: string;
  options: HotelOption[];
  recommendedId: string | null;
  selected: HotelOption | null;
  holdId: string | null;
  searching: boolean;
};

export type Itinerary = { city: string; days: ItineraryDay[] };

type TripState = {
  flightSegments: FlightSegment[];
  hotelStays: HotelStay[];
  itineraries: Itinerary[];

  budget: {
    limit_inr: number;
    spent_inr: number;
    categories: Record<string, number>;
  } | null;

  payment: {
    status: PaymentStatus;
    amount_inr: number | null;
    auth_id: string | null;
    transaction_id: string | null;
  };

  calendar: { count: number; mode: string } | null;
  todos: { count: number; items: { text: string; due_date: string; priority: string }[] } | null;
};

const initialState: TripState = {
  flightSegments: [],
  hotelStays: [],
  itineraries: [],
  budget: null,
  payment: { status: "idle", amount_inr: null, auth_id: null, transaction_id: null },
  calendar: null,
  todos: null,
};

export const flightSegmentKey = (origin: string, destination: string, depart_date: string): string =>
  `${(origin || "").toUpperCase()}-${(destination || "").toUpperCase()}-${depart_date}`;

export const hotelStayKey = (city: string, check_in: string, check_out: string): string =>
  `${(city || "").toLowerCase()}-${check_in}-${check_out}`;

const tripSlice = createSlice({
  name: "trip",
  initialState,
  reducers: {
    /* ── flights ─────────────────────────────────────────────────────── */

    startFlightSearch(
      state,
      action: PayloadAction<{
        key: string;
        origin: string;
        destination: string;
        depart_date: string;
        return_date: string | null;
      }>,
    ) {
      const { key } = action.payload;
      const existing = state.flightSegments.find((s) => s.key === key);
      if (existing) {
        existing.searching = true;
        return;
      }
      state.flightSegments.push({
        key,
        origin: action.payload.origin,
        destination: action.payload.destination,
        depart_date: action.payload.depart_date,
        return_date: action.payload.return_date,
        options: [],
        recommendedId: null,
        selected: null,
        holdId: null,
        searching: true,
      });
    },

    finishFlightSearch(
      state,
      action: PayloadAction<{
        key: string;
        options: FlightOption[];
        recommendedId: string | null;
      }>,
    ) {
      const seg = state.flightSegments.find((s) => s.key === action.payload.key);
      if (!seg) return;
      seg.options = action.payload.options;
      seg.recommendedId = action.payload.recommendedId;
      seg.searching = false;
    },

    /** Find the segment containing this flight_id and mark it selected/held. */
    holdFlightById(
      state,
      action: PayloadAction<{ flight_id: string; hold_id: string }>,
    ) {
      for (const seg of state.flightSegments) {
        const found = seg.options.find((o) => o.flight_id === action.payload.flight_id);
        if (found) {
          seg.selected = found;
          seg.holdId = action.payload.hold_id;
          return;
        }
      }
    },

    /* ── hotels ──────────────────────────────────────────────────────── */

    startHotelSearch(
      state,
      action: PayloadAction<{
        key: string;
        city: string;
        check_in: string;
        check_out: string;
      }>,
    ) {
      const { key } = action.payload;
      const existing = state.hotelStays.find((s) => s.key === key);
      if (existing) {
        existing.searching = true;
        return;
      }
      state.hotelStays.push({
        key,
        city: action.payload.city,
        check_in: action.payload.check_in,
        check_out: action.payload.check_out,
        options: [],
        recommendedId: null,
        selected: null,
        holdId: null,
        searching: true,
      });
    },

    finishHotelSearch(
      state,
      action: PayloadAction<{
        key: string;
        options: HotelOption[];
        recommendedId: string | null;
      }>,
    ) {
      const stay = state.hotelStays.find((s) => s.key === action.payload.key);
      if (!stay) return;
      stay.options = action.payload.options;
      stay.recommendedId = action.payload.recommendedId;
      stay.searching = false;
    },

    holdHotelById(
      state,
      action: PayloadAction<{ hotel_id: string; hold_id: string }>,
    ) {
      for (const stay of state.hotelStays) {
        const found = stay.options.find((o) => o.hotel_id === action.payload.hotel_id);
        if (found) {
          stay.selected = found;
          stay.holdId = action.payload.hold_id;
          return;
        }
      }
    },

    /* ── itinerary (one entry per city; replace by city) ─────────────── */

    upsertItinerary(state, action: PayloadAction<Itinerary>) {
      const idx = state.itineraries.findIndex(
        (it) => it.city.toLowerCase() === action.payload.city.toLowerCase(),
      );
      if (idx >= 0) state.itineraries[idx] = action.payload;
      else state.itineraries.push(action.payload);
    },

    /* ── budget ──────────────────────────────────────────────────────── */

    setBudget(state, action: PayloadAction<TripState["budget"]>) {
      state.budget = action.payload;
    },

    /* ── payment ─────────────────────────────────────────────────────── */

    setPaymentAuth(state, action: PayloadAction<{ auth_id: string; amount_inr: number }>) {
      state.payment.status = "authorized";
      state.payment.auth_id = action.payload.auth_id;
      state.payment.amount_inr = action.payload.amount_inr;
    },
    setPaymentTransaction(
      state,
      action: PayloadAction<{ transaction_id: string; status: string }>,
    ) {
      state.payment.status = action.payload.status === "approved" ? "captured" : "declined";
      state.payment.transaction_id = action.payload.transaction_id;
    },

    /* ── post-booking ────────────────────────────────────────────────── */

    setCalendar(state, action: PayloadAction<{ count: number; mode: string }>) {
      state.calendar = action.payload;
    },
    setTodos(
      state,
      action: PayloadAction<{
        count: number;
        items: { text: string; due_date: string; priority: string }[];
      }>,
    ) {
      state.todos = { count: action.payload.count, items: action.payload.items };
    },

    resetTrip(_state) {
      return initialState;
    },
  },
});

export const {
  startFlightSearch,
  finishFlightSearch,
  holdFlightById,
  startHotelSearch,
  finishHotelSearch,
  holdHotelById,
  upsertItinerary,
  setBudget,
  setPaymentAuth,
  setPaymentTransaction,
  setCalendar,
  setTodos,
  resetTrip,
} = tripSlice.actions;

export const tripReducer = tripSlice.reducer;
