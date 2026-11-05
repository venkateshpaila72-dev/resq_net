import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { 
  Radio, 
  Send, 
  MapPin, 
  Database, 
  Activity, 
  MessageSquare,
  RefreshCw, 
  Search,
  ShieldCheck,
  PhoneForwarded,
  Copy,
  Check,
  Phone
} from 'lucide-react';
import { MapContainer, TileLayer, Marker, Popup } from 'react-leaflet';
import L from 'leaflet';

// Leaflet default pin fix
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

const API_BASE = 'https://resq-net-9akj.onrender.com';

export default function ResQNetDashboard() {
  const [incoming, setIncoming] = useState([]);
  const [outgoing, setOutgoing] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filterQuery, setFilterQuery] = useState('');
  const [activeTab, setActiveTab] = useState('feed'); // 'feed' | 'map'
  const [selectedRecipient, setSelectedRecipient] = useState('');
  const [broadcastMessage, setBroadcastMessage] = useState('');
  const [dispatchStatus, setDispatchStatus] = useState(null);
  const [lastSync, setLastSync] = useState(new Date().toLocaleTimeString());
  const [copied, setCopied] = useState(false);

  const queryPhoneNumber = "+917386597706";
  const displayPhoneNumber = "+91 73865 97706";

  const fetchMessages = async () => {
    try {
      const response = await axios.get(`${API_BASE}/api/messages`);
      if (response.data) {
        setIncoming(response.data.incoming || []);
        setOutgoing(response.data.outgoing || []);
        setLastSync(new Date().toLocaleTimeString());
      }
    } catch (err) {
      console.error("Fetch failed:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMessages();
    const interval = setInterval(fetchMessages, 3500);
    return () => clearInterval(interval);
  }, []);

  const handleCopyPhone = () => {
    navigator.clipboard.writeText(queryPhoneNumber);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const formatTimestamp = (item) => {
    if (item.timestamp && !item.timestamp.includes('T')) {
      return item.timestamp;
    }
    const rawDate = item.timestamp || item.created_at;
    if (rawDate) {
      const d = new Date(rawDate);
      if (!isNaN(d.getTime())) {
        return d.toLocaleString([], {
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit'
        });
      }
    }
    return 'Recent';
  };

  const getTriage = (query = '') => {
    const q = query.toLowerCase();
    if (q.includes('stuck') || q.includes('trapped') || q.includes('flood') || q.includes('danger') || q.includes('fire') || q.includes('gas') || q.includes('leak')) {
      return { 
        label: 'CRITICAL RESCUE', 
        badge: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
        dot: 'bg-rose-500'
      };
    }
    if (q.includes('water') || q.includes('food') || q.includes('ration') || q.includes('starving') || q.includes('shelter')) {
      return { 
        label: 'RATIONS / SHELTER', 
        badge: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
        dot: 'bg-amber-500'
      };
    }
    if (q.includes('doctor') || q.includes('medicine') || q.includes('injury') || q.includes('hospital') || q.includes('fracture') || q.includes('burn')) {
      return { 
        label: 'MEDICAL AID', 
        badge: 'bg-purple-500/10 text-purple-400 border-purple-500/30',
        dot: 'bg-purple-500'
      };
    }
    return { 
      label: 'INFO INQUIRY', 
      badge: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
      dot: 'bg-blue-400'
    };
  };

  const filteredIncoming = incoming.filter(m => 
    (m.query && m.query.toLowerCase().includes(filterQuery.toLowerCase())) ||
    (m.sender && m.sender.includes(filterQuery)) ||
    (m.location && m.location.toLowerCase().includes(filterQuery.toLowerCase()))
  );

  const filteredOutgoing = outgoing.filter(o =>
    (o.reply_text && o.reply_text.toLowerCase().includes(filterQuery.toLowerCase())) ||
    (o.recipient && o.recipient.includes(filterQuery))
  );

  const mapMarkers = incoming.map(msg => {
    let lat = 17.7041;
    let lng = 83.2185;
    let hasExact = false;

    if (msg.exact_gps && msg.exact_gps.latitude && msg.exact_gps.longitude) {
      lat = parseFloat(msg.exact_gps.latitude);
      lng = parseFloat(msg.exact_gps.longitude);
      hasExact = true;
    } else if (msg.location && msg.location.includes(',')) {
      const parts = msg.location.split(',');
      if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
        lat = parseFloat(parts[0]);
        lng = parseFloat(parts[1]);
        hasExact = true;
      }
    }
    return { ...msg, lat, lng, hasExact };
  });

  const handleBroadcast = async (e) => {
    e.preventDefault();
    if (!selectedRecipient || !broadcastMessage) return;

    setDispatchStatus('sending');
    try {
      await axios.post(`${API_BASE}/api/broadcast`, {
        recipient: selectedRecipient,
        message: broadcastMessage
      });
      setDispatchStatus('sent');
      setBroadcastMessage('');
      fetchMessages();
      setTimeout(() => setDispatchStatus(null), 3500);
    } catch (err) {
      setDispatchStatus('error');
    }
  };

  return (
    <div className="min-h-screen bg-[#080c14] text-slate-200 flex flex-col font-sans selection:bg-rose-500 selection:text-white">
      {/* Top Command Bar */}
      <header className="border-b border-slate-800/80 bg-[#0b111e]/90 backdrop-blur-md sticky top-0 z-50 px-6 py-3.5">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="relative">
              <div className="h-10 w-10 rounded-xl bg-gradient-to-tr from-rose-600 to-amber-600 flex items-center justify-center text-white shadow-lg shadow-rose-950/50">
                <Radio className="w-5 h-5 animate-pulse" />
              </div>
              <span className="absolute -top-1 -right-1 flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-3 w-3 bg-rose-500"></span>
              </span>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base font-bold tracking-tight text-white uppercase">ResQ-Net Incident Gateway</h1>
                <span className="text-[10px] bg-rose-500/10 text-rose-400 border border-rose-500/20 px-2 py-0.5 rounded-full font-mono font-bold tracking-wider">
                  DEFCON-RELAY
                </span>
              </div>
              <p className="text-xs text-slate-400 font-mono">Cellular GSM Bridge &bull; Autonomous Groq AI Node</p>
            </div>
          </div>

          {/* Live Node Indicators */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-xs font-mono">
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse"></span>
              <span className="text-slate-300">Gateway: Active</span>
            </div>

            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-xs font-mono">
              <Database className="w-3.5 h-3.5 text-sky-400" />
              <span className="text-slate-300">MongoDB: Connected</span>
            </div>

            <button 
              onClick={fetchMessages}
              title={`Last synced: ${lastSync}`}
              className="p-2 bg-slate-900 hover:bg-slate-800 border border-slate-800 rounded-lg text-slate-300 hover:text-white transition cursor-pointer"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>
      </header>

      {/* Main Operations Frame */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 space-y-6">
        
        {/* ==================== TRY IT OUT SECTION ==================== */}
        <div className="bg-gradient-to-r from-slate-900 via-[#0e1626] to-slate-900 border border-slate-800 rounded-2xl p-5 sm:p-6 shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 right-0 -mr-16 -mt-16 w-64 h-64 rounded-full bg-rose-600/10 blur-3xl pointer-events-none"></div>

          <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-6">
            <div className="space-y-1.5 max-w-xl">
              <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold uppercase bg-rose-500/10 text-rose-400 border border-rose-500/20">
                <span className="w-1.5 h-1.5 rounded-full bg-rose-500 animate-ping"></span>
                Live Public Gateway
              </div>
              <h2 className="text-lg sm:text-xl font-extrabold text-white tracking-tight">
                Try ResQ-Net From Any Mobile Phone
              </h2>
              <p className="text-xs sm:text-sm text-slate-400 leading-relaxed">
                Send an SMS directly to our gateway. Messages must start with keyword <strong className="text-rose-400 font-mono font-bold">ENET</strong>. Works completely offline on the phone without data packs.
              </p>
            </div>

            {/* Gateway Interactive Card */}
            <div className="bg-[#080c14]/90 border border-slate-800/90 rounded-xl p-4 flex flex-col sm:flex-row items-stretch sm:items-center gap-4 w-full lg:w-auto shadow-inner">
              <div className="flex items-center gap-3">
                <div className="p-3 bg-rose-600/20 text-rose-400 rounded-xl border border-rose-500/30">
                  <Phone className="w-5 h-5" />
                </div>
                <div>
                  <span className="text-[10px] uppercase font-mono font-bold text-slate-500 tracking-wider block">
                    Gateway Mobile Number
                  </span>
                  <span className="font-mono text-xl sm:text-2xl font-bold tracking-wider text-white">
                    {displayPhoneNumber}
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2 pt-2 sm:pt-0 sm:border-l sm:border-slate-800 sm:pl-4">
                <button
                  onClick={handleCopyPhone}
                  className="flex-1 sm:flex-none flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono transition cursor-pointer border border-slate-700"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  {copied ? "Copied" : "Copy"}
                </button>

                <a
                  href={`sms:${queryPhoneNumber}?body=ENET%20`}
                  className="flex-1 sm:flex-none flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-gradient-to-r from-rose-600 to-rose-700 hover:from-rose-500 hover:to-rose-600 text-white text-xs font-bold transition shadow-lg shadow-rose-950"
                >
                  <Send className="w-3.5 h-3.5" />
                  Send SMS
                </a>
              </div>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-slate-800/70 flex flex-col sm:flex-row sm:items-center justify-between text-[11px] font-mono text-slate-400 gap-2">
            <div>
              <span className="text-slate-500 font-semibold uppercase mr-2">Example Message:</span>
              <code className="text-slate-200 bg-slate-950 px-2 py-0.5 rounded border border-slate-800">
                <strong className="text-rose-400">ENET</strong> what is the first aid for a severe burn?
              </code>
            </div>
            <span className="text-slate-500 text-[10px]">&bull; Auto-triaged via Groq with sub-second latency</span>
          </div>
        </div>

        {/* KPI Telemetry Banners */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-[#0e1626]/70 border border-slate-800/80 rounded-2xl p-4 relative overflow-hidden backdrop-blur">
            <div className="flex justify-between items-start">
              <div>
                <span className="text-[11px] font-mono uppercase tracking-wider text-slate-400">Total Incoming Inquiries</span>
                <div className="text-3xl font-extrabold font-mono text-white mt-1.5">{incoming.length}</div>
              </div>
              <div className="p-2.5 rounded-xl bg-sky-500/10 text-sky-400 border border-sky-500/20">
                <MessageSquare className="w-5 h-5" />
              </div>
            </div>
            <div className="mt-3 flex items-center text-[11px] text-emerald-400 font-mono">
              <ShieldCheck className="w-3.5 h-3.5 mr-1" /> 100% Ingested in MongoDB
            </div>
          </div>

          <div className="bg-[#0e1626]/70 border border-slate-800/80 rounded-2xl p-4 relative overflow-hidden backdrop-blur">
            <div className="flex justify-between items-start">
              <div>
                <span className="text-[11px] font-mono uppercase tracking-wider text-slate-400">Total AI Dispatches</span>
                <div className="text-3xl font-extrabold font-mono text-emerald-400 mt-1.5">{outgoing.length}</div>
              </div>
              <div className="p-2.5 rounded-xl bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                <Send className="w-5 h-5" />
              </div>
            </div>
            <div className="mt-3 flex items-center text-[11px] text-slate-400 font-mono">
              <Activity className="w-3.5 h-3.5 mr-1 text-emerald-400" /> Groq LLM Grounded
            </div>
          </div>

          <div className="bg-[#0e1626]/70 border border-slate-800/80 rounded-2xl p-4 relative overflow-hidden backdrop-blur">
            <div className="flex justify-between items-start">
              <div>
                <span className="text-[11px] font-mono uppercase tracking-wider text-slate-400">Database Collections</span>
                <div className="text-3xl font-extrabold font-mono text-amber-400 mt-1.5">2</div>
              </div>
              <div className="p-2.5 rounded-xl bg-amber-500/10 text-amber-400 border border-amber-500/20">
                <Database className="w-5 h-5" />
              </div>
            </div>
            <div className="mt-3 flex items-center text-[11px] text-slate-400 font-mono">
              incoming_messages + outgoing_replies
            </div>
          </div>

          <div className="bg-[#0e1626]/70 border border-slate-800/80 rounded-2xl p-4 relative overflow-hidden backdrop-blur">
            <div className="flex justify-between items-start">
              <div>
                <span className="text-[11px] font-mono uppercase tracking-wider text-slate-400">Geolocated Incidents</span>
                <div className="text-3xl font-extrabold font-mono text-rose-400 mt-1.5">
                  {incoming.filter(m => m.location && m.location !== 'Unspecified').length}
                </div>
              </div>
              <div className="p-2.5 rounded-xl bg-rose-500/10 text-rose-400 border border-rose-500/20">
                <MapPin className="w-5 h-5" />
              </div>
            </div>
            <div className="mt-3 flex items-center text-[11px] text-rose-400 font-mono">
              GPS Coordinates Parsed
            </div>
          </div>
        </div>

        {/* View Selection & Search Filter */}
        <div className="bg-[#0e1626]/90 border border-slate-800 p-2.5 rounded-2xl flex flex-col sm:flex-row items-center justify-between gap-3">
          <div className="flex items-center gap-1.5 w-full sm:w-auto">
            <button 
              onClick={() => setActiveTab('feed')}
              className={`px-4 py-2 rounded-xl text-xs font-semibold tracking-wide transition flex items-center gap-2 cursor-pointer ${
                activeTab === 'feed' 
                  ? 'bg-slate-800 text-white shadow-sm border border-slate-700' 
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <MessageSquare className="w-3.5 h-3.5" /> Full Database Records ({incoming.length + outgoing.length})
            </button>
            <button 
              onClick={() => setActiveTab('map')}
              className={`px-4 py-2 rounded-xl text-xs font-semibold tracking-wide transition flex items-center gap-2 cursor-pointer ${
                activeTab === 'map' 
                  ? 'bg-slate-800 text-white shadow-sm border border-slate-700' 
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <MapPin className="w-3.5 h-3.5" /> Tactical Geo-Map
            </button>
          </div>

          <div className="relative w-full sm:w-80">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
            <input 
              type="text" 
              placeholder="Search sender, inquiry, responses..."
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              className="w-full bg-[#080c14] border border-slate-800 rounded-xl pl-9 pr-3 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-slate-700 font-mono"
            />
          </div>
        </div>

        {/* View Mode: Map or Dual Column */}
        {activeTab === 'map' ? (
          <div className="bg-[#0e1626] border border-slate-800 rounded-2xl overflow-hidden h-[620px] flex flex-col shadow-2xl">
            <div className="px-5 py-3 border-b border-slate-800 bg-[#0b111e] flex justify-between items-center">
              <h2 className="text-xs font-mono uppercase text-slate-300 flex items-center gap-2 font-bold">
                <MapPin className="w-4 h-4 text-rose-500" /> Active Coordinate Hotspots
              </h2>
              <span className="text-xs text-slate-400 font-mono">Default Anchor: Visakhapatnam Coastline</span>
            </div>
            <div className="flex-1 w-full z-10">
              <MapContainer center={[17.7041, 83.2185]} zoom={12} style={{ height: '100%', width: '100%' }}>
                <TileLayer
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                {mapMarkers.map((marker, idx) => (
                  <Marker key={idx} position={[marker.lat, marker.lng]}>
                    <Popup>
                      <div className="p-1 text-slate-900 text-xs font-sans">
                        <strong className="block text-rose-600 font-bold">{marker.sender}</strong>
                        <p className="my-1 font-medium">{marker.query}</p>
                        <span className="text-[10px] text-gray-500 block font-mono">Location: {marker.location || 'Unspecified'}</span>
                        <span className="text-[10px] text-gray-400 block font-mono">{formatTimestamp(marker)}</span>
                      </div>
                    </Popup>
                  </Marker>
                ))}
              </MapContainer>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            
            {/* Left: Incoming Inquiries From MongoDB */}
            <div className="bg-[#0e1626]/80 border border-slate-800 rounded-2xl flex flex-col h-[660px] overflow-hidden shadow-2xl backdrop-blur">
              <div className="px-5 py-3.5 border-b border-slate-800 bg-[#0b111e] flex justify-between items-center">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-sky-400 animate-pulse"></span>
                  <h2 className="font-bold text-xs uppercase tracking-wider text-slate-200 font-mono">
                    Incoming Inquiries (All Records)
                  </h2>
                </div>
                <span className="text-[11px] bg-slate-900 border border-slate-800 text-slate-400 px-2 py-0.5 rounded-md font-mono">
                  {filteredIncoming.length} records
                </span>
              </div>

              <div className="flex-1 overflow-y-auto p-4 space-y-3">
                {filteredIncoming.length === 0 ? (
                  <div className="text-center py-32 text-slate-500 text-xs font-mono">
                    No incoming records found in database.
                  </div>
                ) : (
                  filteredIncoming.map((msg, idx) => {
                    const triage = getTriage(msg.query || '');
                    return (
                      <div 
                        key={msg._id || idx} 
                        className="bg-[#080c14]/90 border border-slate-800/90 hover:border-slate-700 rounded-xl p-4 space-y-2.5 transition group"
                      >
                        <div className="flex justify-between items-start">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs font-bold text-sky-400">{msg.sender}</span>
                            <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono font-bold tracking-wider ${triage.badge}`}>
                              {triage.label}
                            </span>
                          </div>
                          <span className="text-[11px] text-slate-500 font-mono">{formatTimestamp(msg)}</span>
                        </div>

                        <p className="text-xs text-slate-200 leading-relaxed bg-[#0e1626]/80 p-3 rounded-lg border border-slate-800/60 font-sans">
                          {msg.query || '(Empty Query Text)'}
                        </p>

                        <div className="flex justify-between items-center text-[11px] pt-1 border-t border-slate-900">
                          <div className="flex items-center gap-1.5 text-slate-400">
                            <MapPin className="w-3.5 h-3.5 text-rose-500" />
                            <span className="font-mono text-[11px]">{msg.location || 'Unspecified'}</span>
                          </div>
                          <button 
                            onClick={() => setSelectedRecipient(msg.sender)}
                            className="text-sky-400 hover:text-sky-300 font-mono text-xs flex items-center gap-1 hover:underline cursor-pointer"
                          >
                            <PhoneForwarded className="w-3 h-3" /> Quick Dispatch
                          </button>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>

            {/* Right: Outgoing AI Dispatches & Manual Broadcast Box */}
            <div className="flex flex-col gap-6 h-[660px]">
              
              {/* Outgoing Replies From MongoDB */}
              <div className="bg-[#0e1626]/80 border border-slate-800 rounded-2xl flex flex-col flex-1 overflow-hidden shadow-2xl backdrop-blur">
                <div className="px-5 py-3.5 border-b border-slate-800 bg-[#0b111e] flex justify-between items-center">
                  <div className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full bg-emerald-400"></span>
                    <h2 className="font-bold text-xs uppercase tracking-wider text-slate-200 font-mono">
                      Outgoing AI Dispatches (All Records)
                    </h2>
                  </div>
                  <span className="text-[11px] bg-slate-900 border border-slate-800 text-slate-400 px-2 py-0.5 rounded-md font-mono">
                    {filteredOutgoing.length} records
                  </span>
                </div>

                <div className="flex-1 overflow-y-auto p-4 space-y-3">
                  {filteredOutgoing.length === 0 ? (
                    <div className="text-center py-20 text-slate-500 text-xs font-mono">
                      No dispatched records found in database.
                    </div>
                  ) : (
                    filteredOutgoing.map((out, idx) => (
                      <div key={out._id || idx} className="bg-[#080c14]/90 border border-slate-800/90 rounded-xl p-4 space-y-2.5">
                        <div className="flex justify-between items-start">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs font-bold text-emerald-400">To: {out.recipient}</span>
                            <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full border ${
                              out.status === 'SENT' 
                                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' 
                                : 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                            }`}>
                              {out.status || 'SENT'}
                            </span>
                          </div>
                          <span className="text-[11px] text-slate-500 font-mono">{formatTimestamp(out)}</span>
                        </div>
                        <p className="text-xs text-slate-200 leading-relaxed bg-[#0e1626]/80 p-3 rounded-lg border border-slate-800/60 font-sans">
                          {out.reply_text || '(Empty Response Text)'}
                        </p>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* Responder Manual Dispatch Console */}
              <div className="bg-[#0e1626] border border-slate-800 rounded-2xl p-4 shadow-xl">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider font-mono flex items-center gap-1.5">
                    <Send className="w-3.5 h-3.5 text-rose-500" /> Command Responder Broadcast
                  </h3>
                  {dispatchStatus === 'sent' && (
                    <span className="text-[11px] font-mono text-emerald-400">Transmitted Successfully</span>
                  )}
                  {dispatchStatus === 'error' && (
                    <span className="text-[11px] font-mono text-rose-400">Transmission Failed</span>
                  )}
                </div>

                <form onSubmit={handleBroadcast} className="space-y-3">
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <input 
                      type="text" 
                      placeholder="Target Mobile (+91...)"
                      value={selectedRecipient}
                      onChange={(e) => setSelectedRecipient(e.target.value)}
                      required
                      className="col-span-1 bg-[#080c14] border border-slate-800 rounded-xl px-3 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-slate-700 font-mono"
                    />
                    <input 
                      type="text" 
                      placeholder="Dispatch evacuation advisory, relief shelter update..."
                      value={broadcastMessage}
                      onChange={(e) => setBroadcastMessage(e.target.value)}
                      required
                      maxLength={160}
                      className="col-span-2 bg-[#080c14] border border-slate-800 rounded-xl px-3 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-slate-700 font-sans"
                    />
                  </div>
                  <div className="flex justify-between items-center pt-1">
                    <span className="text-[10px] text-slate-500 font-mono">GSM SMS Limit: 160 chars</span>
                    <button 
                      type="submit" 
                      disabled={dispatchStatus === 'sending'}
                      className="bg-gradient-to-r from-rose-600 to-rose-700 hover:from-rose-500 hover:to-rose-600 text-white text-xs font-bold px-4 py-2 rounded-xl transition flex items-center gap-1.5 disabled:opacity-50 cursor-pointer shadow-lg shadow-rose-950"
                    >
                      {dispatchStatus === 'sending' ? 'Transmitting...' : 'Send Emergency SMS'}
                    </button>
                  </div>
                </form>
              </div>

            </div>
          </div>
        )}
      </main>
    </div>
  );
}