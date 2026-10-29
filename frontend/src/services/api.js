import axios from "axios";

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL || "http://localhost:8000/api/v1", withCredentials: true });
export const getDashboard = () => Promise.all([api.get("/dashboard/summary"), api.get("/dashboard/trends")]);
export const getStudents = (search = "") => api.get("/students", { params: search ? { search } : {} });
export const getClasses = () => api.get("/classes");
export const getSessions = () => api.get("/sessions");

