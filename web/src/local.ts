// The page inside tTerm for the computer before signing in (from 0.1.4),
// as approved on the canvas of 21.10 (version 63). The app's engine says so
// in /api/state ("local": true); tTerm Web itself never does, so in the
// browser the page is always signed in. Before signing in there is this
// computer only: Personal with it and its terminals, where no machine is
// added and no project made; the account at the bottom of the sidebar says
// Not signed in, and its menu has Settings and Sign in.

import { createContext } from "react";

/** True in the app before signing in. */
export const LocalOnly = createContext(false);
