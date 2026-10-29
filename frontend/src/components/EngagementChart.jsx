import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function EngagementChart({ data }) {
  return <div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data}><defs><linearGradient id="shade" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.35}/><stop offset="100%" stopColor="#8b5cf6" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="#26324c" vertical={false}/><XAxis dataKey="label" stroke="#64748b"/><YAxis domain={[0,100]} stroke="#64748b"/><Tooltip contentStyle={{background:"#10172a",border:"1px solid #334155"}}/><Area type="monotone" dataKey="demo" name="Synthetic demo" stroke="#a78bfa" fill="url(#shade)" connectNulls/><Area type="monotone" dataKey="recorded" name="Recorded" stroke="#38bdf8" fill="transparent" connectNulls/></AreaChart></ResponsiveContainer></div>;
}
