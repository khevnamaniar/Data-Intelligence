import { createContext, useContext } from "react";
import { User } from "./api";

export type AuthCtx = { user: User | null; setUser: (u: User | null) => void; logout: () => Promise<void> };
export const Auth = createContext<AuthCtx>({ user: null, setUser: () => {}, logout: async () => {} });
export const useAuth = () => useContext(Auth);
