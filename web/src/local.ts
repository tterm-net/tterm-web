// The page inside tTerm for the computer before signing in (from 0.1.4),
// as approved on the canvas of 21.10 (version 63). The app's engine says so
// in /api/state ("local": true); tTerm Web itself never does, so in the
// browser the page is always signed in. Before signing in there is this
// computer only: Personal with it and its terminals; the account at the
// bottom of the sidebar says Not signed in, and its menu has Settings and
// Sign in. New project and Add machine are there as after signing in, and
// from 0.1.5 say what signing in brings (canvas of 22.10, version 65;
// NeedAccount.tsx).

import { createContext } from "react";

/** True in the app before signing in. */
export const LocalOnly = createContext(false);
