import { useEffect, useRef, useState } from "react";
import { api } from "../services/api";

const permissionMessage = error => {
  if (error?.name === "NotAllowedError" || error?.name === "SecurityError") return "Permission was denied. Allow camera or microphone access in the browser, then try again.";
  if (error?.name === "NotFoundError" || error?.name === "DevicesNotFoundError") return "No compatible camera or microphone was found.";
  if (error?.name === "NotReadableError" || error?.name === "TrackStartError") return "The device is busy or unavailable. Close other apps using it and retry.";
  if (error?.name === "OverconstrainedError") return "The selected device is unavailable or does not support the requested settings. Choose another device.";
  if (!navigator.mediaDevices?.getUserMedia) return "This browser does not support local media capture. Open the app on localhost in a current browser.";
  return error?.message || "The device could not be started.";
};

export default function LiveCapture({ session }) {
  const videoRef = useRef(null), cameraStream = useRef(null), micStream = useRef(null), cameraTimer = useRef(null), micTimer = useRef(null), audioContext = useRef(null), sendingFrame = useRef(false), sendingAudio = useRef(false);
  const [camera, setCamera] = useState("off"), [microphone, setMicrophone] = useState("off"), [error, setError] = useState(""), [cameraError, setCameraError] = useState(""), [microphoneError, setMicrophoneError] = useState(""), [faces, setFaces] = useState("—"), [pose, setPose] = useState(null), [activity, setActivity] = useState("—"), [timestamp, setTimestamp] = useState(""), [capability, setCapability] = useState(null), [cameras,setCameras]=useState([]), [microphones,setMicrophones]=useState([]), [selectedCamera,setSelectedCamera]=useState(""), [selectedMicrophone,setSelectedMicrophone]=useState(""), [cameraPermission,setCameraPermission]=useState("unknown"), [microphonePermission,setMicrophonePermission]=useState("unknown"), [cameraStreamStatus,setCameraStreamStatus]=useState("not started"), [microphoneStreamStatus,setMicrophoneStreamStatus]=useState("not started");

  async function refreshDevices() {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const videoInputs = devices.filter(item => item.kind === "videoinput"), audioInputs = devices.filter(item => item.kind === "audioinput");
      setCameras(videoInputs); setMicrophones(audioInputs);
      if (!selectedCamera && videoInputs[0]) setSelectedCamera(videoInputs[0].deviceId);
      if (!selectedMicrophone && audioInputs[0]) setSelectedMicrophone(audioInputs[0].deviceId);
    } catch (e) { setError(e.message || "Connected media devices could not be listed."); }
  }

  useEffect(() => { api.get("/capabilities").then(r => setCapability(r.data)).catch(() => setCapability(null)); refreshDevices();
    if(navigator.permissions?.query){navigator.permissions.query({name:"camera"}).then(p=>setCameraPermission(p.state)).catch(()=>{});navigator.permissions.query({name:"microphone"}).then(p=>setMicrophonePermission(p.state)).catch(()=>{});}
  }, []);

  async function startCamera() {
    setError(""); setCameraError("");
    if (camera !== "off") return;
    if (!navigator.mediaDevices?.getUserMedia) { setCameraStreamStatus("unsupported"); setCameraError(permissionMessage()); return; }
    setCamera("starting"); setCameraStreamStatus("requesting permission");
    try {
      const video = { width: { ideal: 640 }, height: { ideal: 480 } };
      if (selectedCamera) video.deviceId = { exact: selectedCamera };
      const stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
      cameraStream.current = stream;
      videoRef.current.srcObject = stream;
      await videoRef.current.play();
      setCamera("on"); setCameraPermission("granted"); setCameraStreamStatus(stream.getVideoTracks()[0]?.readyState === "live" ? "stream started" : "stream unavailable");
      await refreshDevices();
      cameraTimer.current = window.setInterval(async () => {
        const video = videoRef.current;
        if (!video || video.readyState < 2 || sendingFrame.current) return;
        sendingFrame.current = true;
        try {
          const canvas = document.createElement("canvas");
          canvas.width = Math.min(video.videoWidth, 640);
          canvas.height = Math.round(video.videoHeight * canvas.width / video.videoWidth);
          canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
          const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.65));
          if (!blob) return;
          const payload = new FormData(); payload.append("file", blob, "camera-frame.jpg");
          const result = await api.post(`/sessions/${session.id}/media/frame`, payload, { headers: { "Content-Type": "multipart/form-data" } });
          setFaces(result.data.face_detections); setPose(result.data.avg_yaw_proxy == null ? null : `orientation proxy ${result.data.avg_yaw_proxy.toFixed(2)}, ${result.data.avg_pitch_proxy.toFixed(2)}`); setTimestamp(new Date(result.data.recorded_at).toLocaleTimeString());
        } catch (e) { setError(e.response?.data?.detail || "Camera frame processing stopped. Check that the session is active."); stopCamera(); }
        finally { sendingFrame.current = false; }
      }, 1000);
    } catch (e) { cameraStream.current?.getTracks().forEach(track=>track.stop()); cameraStream.current=null; setCamera("off"); setCameraStreamStatus("failed"); if(e?.name==="NotAllowedError"||e?.name==="SecurityError")setCameraPermission("denied"); setCameraError(`${e?.name||"CameraError"}: ${permissionMessage(e)}`); }
  }

  function stopCamera() {
    if (cameraTimer.current) window.clearInterval(cameraTimer.current);
    cameraTimer.current = null;
    cameraStream.current?.getTracks().forEach(track => track.stop());
    cameraStream.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setCamera("off"); setCameraStreamStatus("stopped");
  }

  async function startMicrophone() {
    setError(""); setMicrophoneError("");
    if (microphone !== "off") return;
    if (!navigator.mediaDevices?.getUserMedia) { setMicrophoneStreamStatus("unsupported"); setMicrophoneError(permissionMessage()); return; }
    setMicrophone("starting"); setMicrophoneStreamStatus("requesting permission");
    try {
      const audio = { echoCancellation: true, noiseSuppression: true };
      if (selectedMicrophone) audio.deviceId = { exact: selectedMicrophone };
      const stream = await navigator.mediaDevices.getUserMedia({ audio, video: false });
      micStream.current = stream;
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) throw new Error("Web Audio analysis is unavailable in this browser.");
      const context = new Context(); audioContext.current = context; await context.resume();
      const source = context.createMediaStreamSource(stream), analyser = context.createAnalyser();
      analyser.fftSize = 1024; source.connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      setMicrophone("on"); setMicrophonePermission("granted"); setMicrophoneStreamStatus(stream.getAudioTracks()[0]?.readyState === "live" ? "stream started" : "stream unavailable");
      await refreshDevices();
      micTimer.current = window.setInterval(async () => {
        analyser.getFloatTimeDomainData(samples);
        let sum = 0; for (const sample of samples) sum += sample * sample;
        const rms = Math.sqrt(sum / samples.length);
        const level = Math.min(100, Math.round(rms * 5000));
        const active = rms >= 0.02;
        setActivity(level); setTimestamp(new Date().toLocaleTimeString());
        if(sendingAudio.current)return;
        sendingAudio.current=true;
        try { await api.post(`/sessions/${session.id}/media/audio-activity`, { duration_seconds: 1, activity_percentage: active ? 100 : 0 }); }
        catch (e) { setMicrophoneError(e.response?.data?.detail || "Microphone activity could not be saved. Check that the session is active."); stopMicrophone(); }
        finally { sendingAudio.current=false; }
      }, 1000);
    } catch (e) { micStream.current?.getTracks().forEach(track=>track.stop()); micStream.current=null; stopMicrophone(); setMicrophoneStreamStatus("failed"); if(e?.name==="NotAllowedError"||e?.name==="SecurityError")setMicrophonePermission("denied"); setMicrophoneError(`${e?.name||"MicrophoneError"}: ${permissionMessage(e)}`); }
  }

  function stopMicrophone() {
    if (micTimer.current) window.clearInterval(micTimer.current);
    micTimer.current = null;
    micStream.current?.getTracks().forEach(track => track.stop()); micStream.current = null;
    if (audioContext.current && audioContext.current.state !== "closed") audioContext.current.close().catch(() => {});
    audioContext.current = null; setMicrophone("off"); setMicrophoneStreamStatus("stopped");
  }

  useEffect(() => () => {
    if (cameraTimer.current) window.clearInterval(cameraTimer.current);
    if (micTimer.current) window.clearInterval(micTimer.current);
    cameraStream.current?.getTracks().forEach(track => track.stop());
    micStream.current?.getTracks().forEach(track => track.stop());
    if (audioContext.current && audioContext.current.state !== "closed") audioContext.current.close().catch(() => {});
  }, []);

  const mediaSupported=Boolean(navigator.mediaDevices?.getUserMedia);
  return <section className="panel live-capture"><div className="panel-heading"><div><h3>Live local sensors · {session.title}</h3><span>Capture begins only after you press Start; device access stays in this browser session.</span></div></div>
    <div className="notice">No video or audio recording is saved. Camera frames are processed and discarded; the microphone sends only RMS activity levels and timestamps. This is not speaker detection or a measure of attention or understanding.</div>
    <div className="sensor-grid"><div className="sensor-card"><h4>Camera diagnostic</h4><video ref={videoRef} className="camera-preview" autoPlay muted playsInline aria-label="Live camera preview"/><label className="device-select">Camera device<select aria-label="Camera device" disabled={camera!=="off"} value={selectedCamera} onChange={e=>setSelectedCamera(e.target.value)}><option value="">Default camera</option>{cameras.map((device,index)=><option key={device.deviceId||index} value={device.deviceId}>{device.label||`Camera ${index+1}`}</option>)}</select></label><div className="sensor-actions">{camera === "off" ? <button className="button" onClick={startCamera}>Start camera</button> : <button className="button secondary" disabled={camera==="starting"} onClick={stopCamera}>{camera==="starting"?"Starting…":"Stop camera"}</button>}<span>{camera === "on" ? `Anonymous faces: ${faces} · ${timestamp}` : camera==="starting"?"Waiting for browser permission…":"Camera is off"}</span></div>{pose&&<p className="muted">{pose} · approximate visual proxy, not attention</p>}{cameraError&&<div className="error-line" role="alert">{cameraError}</div>}</div>
    <div className="sensor-card"><h4>Microphone diagnostic</h4><strong>{microphone === "on" ? `${activity}% RMS level` : microphone==="starting"?"Requesting permission…":"Off"}</strong><p>Activity windows use a simple RMS threshold. Audio is not stored.</p><label className="device-select">Microphone device<select aria-label="Microphone device" disabled={microphone!=="off"} value={selectedMicrophone} onChange={e=>setSelectedMicrophone(e.target.value)}><option value="">Default microphone</option>{microphones.map((device,index)=><option key={device.deviceId||index} value={device.deviceId}>{device.label||`Microphone ${index+1}`}</option>)}</select></label><div className="sensor-actions">{microphone === "off" ? <button className="button" onClick={startMicrophone}>Start microphone test</button> : <button className="button secondary" disabled={microphone==="starting"} onClick={stopMicrophone}>{microphone==="starting"?"Starting…":"Stop microphone"}</button>}<span>{microphone === "on" ? `Input level updated ${timestamp}` : "No audio captured"}</span></div>{microphoneError&&<div className="error-line" role="alert">{microphoneError}</div>}</div></div>
    <section className="sensor-diagnostics" aria-live="polite"><h4>Device status</h4><div><span>Media API</span><b>{mediaSupported?"Available":"Unavailable"}</b><span>Camera devices</span><b>{cameras.length?`${cameras.length} found`:"None found or permission not granted"}</b><span>Camera permission</span><b>{cameraPermission}</b><span>Camera stream</span><b>{cameraStream.current?.getVideoTracks()[0]?.readyState||cameraStreamStatus}</b><span>Microphone devices</span><b>{microphones.length?`${microphones.length} found`:"None found or permission not granted"}</b><span>Microphone permission</span><b>{microphonePermission}</b><span>Microphone stream</span><b>{micStream.current?.getAudioTracks()[0]?.readyState||microphoneStreamStatus}</b></div><button className="text-button" onClick={refreshDevices}>Refresh device list</button></section>
    {error && <div className="error-line" role="alert">{error}</div>}<p className="hint">Landmarks: {capability?.vision?.landmark_status || "Checking local model status…"}. Identity matching: unavailable. Attendance remains manual and instructor reviewed.</p></section>;
}
