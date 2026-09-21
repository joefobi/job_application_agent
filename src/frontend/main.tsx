import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  ArrowLeft,
  Check,
  Columns3,
  CopyCheck,
  Database,
  ExternalLink,
  LogOut,
  RefreshCcw,
  Save,
  Search,
  Send,
  Trash2,
  X,
  UserRound,
} from "lucide-react";

import {
  CandidateProfile,
  DashboardData,
  JobStatus,
  UserSession,
  clearSession,
  emptyDashboardData,
  emptyProfile,
  fetchDashboardData,
  fetchProfile,
  isProfileComplete,
  loadStoredSession,
  runDiscovery,
  saveProfile,
  storeSession,
  updateJobStatus,
} from "./api";
import "./styles.css";

type Screen = "profile" | "pipeline" | "duplicates" | "ledger";
type ConnectionState = "loading" | "connected" | "local";
type SaveState = "idle" | "saving" | "saved" | "invalid" | "error";
type DiscoveryState = "idle" | "running" | "completed" | "local" | "error";

interface DiscoveryStatus {
  state: DiscoveryState;
  message: string;
  checkedAt?: string;
}

interface GoogleCredentialResponse {
  credential: string;
}

interface GoogleAccounts {
  accounts: {
    id: {
      initialize: (config: {
        client_id: string;
        callback: (response: GoogleCredentialResponse) => void;
      }) => void;
      renderButton: (
        element: HTMLElement,
        options: {
          theme: "outline" | "filled_blue" | "filled_black";
          size: "large" | "medium" | "small";
          type?: "standard" | "icon";
          width?: number;
        },
      ) => void;
    };
  };
}

declare global {
  interface Window {
    google?: GoogleAccounts;
    jobAgentRoot?: Root;
  }
}

const screens: Array<{
  id: Screen;
  label: string;
  icon: React.ComponentType<{ size?: number }>;
}> = [
  { id: "profile", label: "Profile", icon: UserRound },
  { id: "pipeline", label: "Pipeline", icon: Columns3 },
  { id: "duplicates", label: "Duplicates", icon: CopyCheck },
  { id: "ledger", label: "Ledger", icon: Database },
];

const pipelineStatuses: JobStatus[] = [
  "discovered",
  "needs_review",
  "approved_to_apply",
  "applied",
  "interviewing",
];

const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined;

function App() {
  const [session, setSession] = useState<UserSession | null>(() =>
    loadStoredSession(),
  );
  const [profile, setProfile] = useState<CandidateProfile | null>(null);
  const [dashboard, setDashboard] = useState<DashboardData>(emptyDashboardData);
  const [activeScreen, setActiveScreen] = useState<Screen>("pipeline");
  const [connection, setConnection] = useState<ConnectionState>("loading");
  const [ledgerSearch, setLedgerSearch] = useState("");
  const [ledgerStatus, setLedgerStatus] = useState<JobStatus | "all">("all");
  const [discoveryStatus, setDiscoveryStatus] = useState<DiscoveryStatus>({
    message: "Discovery has not run in this session.",
    state: "idle",
  });

  useEffect(() => {
    if (!session) {
      setConnection("local");
      return;
    }

    const activeSession = session;
    let cancelled = false;
    async function loadData() {
      setConnection("loading");
      const [profileResult, dashboardResult] = await Promise.all([
        fetchProfile(activeSession),
        fetchDashboardData(activeSession),
      ]);
      if (cancelled) {
        return;
      }
      setProfile(profileResult.data ?? emptyProfile(activeSession));
      setDashboard(dashboardResult.data);
      setConnection(
        profileResult.fromApi || dashboardResult.fromApi ? "connected" : "local",
      );
      if (!profileResult.data || !isProfileComplete(profileResult.data)) {
        setActiveScreen("profile");
      }
    }

    loadData();
    return () => {
      cancelled = true;
    };
  }, [session]);

  const counts = useMemo(() => {
    const jobs = dashboard.jobs;
    return {
      profile: undefined,
      pipeline: jobs.length,
      duplicates: dashboard.duplicates.length,
      ledger: dashboard.ledger.length,
    };
  }, [dashboard]);

  if (!session) {
    return <LoginScreen onAuthenticated={setSession} />;
  }

  if (!profile || connection === "loading") {
    return <LoadingShell />;
  }

  const currentSession = session;

  const filteredLedger = dashboard.ledger.filter((entry) => {
    const query = ledgerSearch.trim().toLowerCase();
    const matchesText =
      !query ||
      `${entry.company} ${entry.title}`.toLowerCase().includes(query);
    const matchesStatus = ledgerStatus === "all" || entry.status === ledgerStatus;
    return matchesText && matchesStatus;
  });

  async function handleProfileSave(
    nextProfile: CandidateProfile,
  ): Promise<CandidateProfile> {
    const result = await saveProfile(nextProfile, currentSession);
    setProfile(result.data);
    setConnection(result.fromApi ? "connected" : "local");
    return result.data;
  }

  async function refreshDiscovery() {
    if (discoveryStatus.state === "running") {
      return;
    }
    if (!profile) {
      setActiveScreen("profile");
      return;
    }
    setDiscoveryStatus({
      message:
        "Discovery is running. The agent is syncing your profile, searching sources, deduping jobs, scoring matches, and refreshing the pipeline.",
      state: "running",
    });
    let runReachedApi = true;
    const syncedProfile = await saveProfile(profile, currentSession);
    setProfile(syncedProfile.data);
    setConnection(syncedProfile.fromApi ? "connected" : "local");
    try {
      await runDiscovery(currentSession);
    } catch {
      runReachedApi = false;
      setConnection("local");
    }
    try {
      const result = await fetchDashboardData(currentSession);
      const checkedAt = formatTime(new Date());
      setDashboard(result.data);
      setConnection(result.fromApi ? "connected" : "local");
      if (runReachedApi && result.fromApi) {
        setDiscoveryStatus({
          checkedAt,
          message: `Discovery finished and the pipeline was refreshed. ${result.data.jobs.length} job${result.data.jobs.length === 1 ? "" : "s"} are currently in the pipeline.`,
          state: "completed",
        });
        return;
      }
      setDiscoveryStatus({
        checkedAt,
        message:
          "Discovery could not reach the backend, so the dashboard is showing local data. Start the local API on localhost:8000, then run discovery again.",
        state: "local",
      });
    } catch {
      setConnection("local");
      setDiscoveryStatus({
        checkedAt: formatTime(new Date()),
        message:
          "Discovery started but the dashboard could not refresh. Try again after the backend finishes, or check the API logs.",
        state: "error",
      });
    }
  }

  async function setJobStatus(jobId: string, nextStatus: JobStatus) {
    try {
      await updateJobStatus(jobId, nextStatus, currentSession);
      setConnection("connected");
      const result = await fetchDashboardData(currentSession);
      setDashboard(result.data);
      setConnection(result.fromApi ? "connected" : "local");
    } catch {
      setConnection("local");
      setDiscoveryStatus({
        checkedAt: formatTime(new Date()),
        message:
          "Status update was not saved because the backend could not be reached. The pipeline still shows the last persisted state.",
        state: "error",
      });
      return;
    }
  }

  function signOut() {
    clearSession();
    setSession(null);
    setProfile(null);
    setDashboard(emptyDashboardData);
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">JA</div>
          <div>
            <div className="brand-name">Agent Console</div>
            <div className="brand-sub">{session.email}</div>
          </div>
        </div>
        <nav aria-label="Agent console">
          {screens.map((screen) => {
            const Icon = screen.icon;
            return (
              <button
                className={`nav-item ${activeScreen === screen.id ? "active" : ""}`}
                key={screen.id}
                onClick={() => setActiveScreen(screen.id)}
                type="button"
              >
                <span className="nav-label">
                  <Icon size={16} />
                  {screen.label}
                </span>
                {counts[screen.id] !== undefined && (
                  <span className="nav-count">{counts[screen.id]}</span>
                )}
              </button>
            );
          })}
        </nav>
        <button className="nav-item sign-out" onClick={signOut} type="button">
          <span className="nav-label">
            <LogOut size={16} />
            Sign out
          </span>
        </button>
        <div className="sidebar-foot">
          localhost:8000 · {connection === "connected" ? "connected" : "local data"}
        </div>
      </aside>

      <main>
        {activeScreen === "profile" && (
          <ProfileScreen
            onSave={handleProfileSave}
            profile={profile}
            requiresSetup={!isProfileComplete(profile)}
          />
        )}
        {activeScreen === "pipeline" && (
          <PipelineScreen
            data={dashboard}
            onRefresh={refreshDiscovery}
            onStatusChange={setJobStatus}
            status={discoveryStatus}
          />
        )}
        {activeScreen === "duplicates" && <DuplicatesScreen data={dashboard} />}
        {activeScreen === "ledger" && (
          <LedgerScreen
            entries={filteredLedger}
            onSearchChange={setLedgerSearch}
            onStatusChange={setLedgerStatus}
            search={ledgerSearch}
            selectedStatus={ledgerStatus}
          />
        )}
      </main>
    </div>
  );
}

function LoginScreen({
  onAuthenticated,
}: {
  onAuthenticated: (session: UserSession) => void;
}) {
  const googleButtonRef = useRef<HTMLDivElement | null>(null);
  const [googleState, setGoogleState] = useState<
    "missing_client" | "loading" | "ready" | "error"
  >(googleClientId ? "loading" : "missing_client");

  useEffect(() => {
    if (!googleClientId) {
      return;
    }
    const existingScript = document.querySelector<HTMLScriptElement>(
      'script[src="https://accounts.google.com/gsi/client"]',
    );
    if (existingScript) {
      setGoogleState(window.google ? "ready" : "loading");
      return;
    }
    const script = document.createElement("script");
    script.async = true;
    script.defer = true;
    script.src = "https://accounts.google.com/gsi/client";
    script.onload = () => setGoogleState("ready");
    script.onerror = () => setGoogleState("error");
    document.head.appendChild(script);
  }, []);

  useEffect(() => {
    if (
      !googleClientId ||
      googleState !== "ready" ||
      !window.google ||
      !googleButtonRef.current
    ) {
      return;
    }
    googleButtonRef.current.innerHTML = "";
    window.google.accounts.id.initialize({
      callback: (response) => {
        const user = decodeGoogleCredential(response.credential);
        const session = {
          idToken: response.credential,
          email: user.email,
          name: user.name,
          picture: user.picture,
        };
        storeSession(session);
        onAuthenticated(session);
      },
      client_id: googleClientId,
    });
    window.google.accounts.id.renderButton(googleButtonRef.current, {
      size: "large",
      theme: "outline",
      type: "standard",
      width: 280,
    });
  }, [googleState, onAuthenticated]);

  function startLocalSession() {
    const session = {
      email: "local@example.com",
      idToken: "local-dev-token",
      name: "Local User",
    };
    storeSession(session);
    onAuthenticated(session);
  }

  return (
    <div className="landing-wrap">
      <section className="landing-left">
        <div className="brand-mark landing-mark">JA</div>
        <div className="hero-eyebrow">AGENT CONSOLE</div>
        <h1>Your job search, run like a system you can audit.</h1>
        <p className="hero-desc">
          The agent finds postings, scores them against your actual profile, and
          drafts truthful materials. Nothing gets submitted until you approve it.
        </p>

        <div className="mini-pipe" aria-hidden="true">
          <div className="mini-col">
            <div className="mini-col-label">DISCOVERED</div>
            <div className="mini-card">
              <p className="mini-card-title">Backend Engineer</p>
              <p className="mini-card-sub">ExampleCo · Remote</p>
              <span className="mini-score">87</span>
            </div>
          </div>
          <div className="mini-col">
            <div className="mini-col-label">NEEDS REVIEW</div>
            <div className="mini-card">
              <p className="mini-card-title">Infra Engineer</p>
              <p className="mini-card-sub">Ridgeline Labs</p>
              <span className="mini-score">71</span>
            </div>
          </div>
          <div className="mini-col">
            <div className="mini-col-label">APPLIED</div>
            <div className="mini-card">
              <p className="mini-card-title">Founding Engineer</p>
              <p className="mini-card-sub">Formal Systems</p>
              <span className="mini-score">85</span>
            </div>
          </div>
        </div>

        <p className="hero-foot">
          <strong>Every application logged.</strong> Duplicate checks run before
          anything is sent twice.
        </p>
      </section>

      <section className="landing-right">
        <div className="auth-card">
          <div className="auth-head">
            <p className="auth-title">Sign in</p>
            <p className="auth-sub">
              Connect an account to load your profile and pipeline.
            </p>
          </div>

          {googleClientId ? (
            <div className="google-auth-slot">
              {googleState === "loading" && (
                <div className="oauth-btn oauth-btn-static">
                  <GoogleIcon />
                  Loading Google sign-in
                </div>
              )}
              {googleState === "error" && (
                <div className="auth-error">
                  Google sign-in could not load. Check your network and
                  authorized JavaScript origin.
                </div>
              )}
              <div className="google-button" ref={googleButtonRef} />
            </div>
          ) : (
            <button className="oauth-btn" onClick={startLocalSession} type="button">
              <GoogleIcon />
              Continue locally
            </button>
          )}

          {!googleClientId && (
            <p className="auth-note">
              Set <code>VITE_GOOGLE_CLIENT_ID</code> in <code>.env.local</code>{" "}
              to enable Google sign-in.
            </p>
          )}

          <p className="legal">
            By continuing you agree to use this local development build for your
            own job-search data.
          </p>
        </div>
      </section>
    </div>
  );
}

function GoogleIcon() {
  return (
    <svg aria-hidden="true" className="oauth-icon" viewBox="0 0 48 48">
      <path
        d="M43.6 20.5H42V20H24v8h11.3C33.7 32.9 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.1 8 3l5.7-5.7C34.6 6.2 29.6 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.7-.4-3.5z"
        fill="#FFC107"
      />
      <path
        d="M6.3 14.7l6.6 4.8C14.6 15.9 18.9 13 24 13c3.1 0 5.8 1.1 8 3l5.7-5.7C34.6 6.2 29.6 4 24 4 16.3 4 9.6 8.3 6.3 14.7z"
        fill="#FF3D00"
      />
      <path
        d="M24 44c5.5 0 10.4-2.1 14.1-5.6l-6.5-5.5C29.6 34.7 27 35.5 24 35.5c-5.3 0-9.7-3-11.3-7.9l-6.6 5.1C9.5 39.6 16.2 44 24 44z"
        fill="#4CAF50"
      />
      <path
        d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.3 4.2-4.2 5.5l6.5 5.5C40.9 36.6 44 31 44 24c0-1.3-.1-2.7-.4-3.5z"
        fill="#1976D2"
      />
    </svg>
  );
}

function LoadingShell() {
  return (
    <div className="loading-shell">
      <div className="brand-mark">JA</div>
      <div>Loading agent console</div>
    </div>
  );
}

function ProfileScreen({
  onSave,
  profile,
  requiresSetup,
}: {
  onSave: (profile: CandidateProfile) => Promise<CandidateProfile>;
  profile: CandidateProfile;
  requiresSetup: boolean;
}) {
  const [draft, setDraft] = useState<CandidateProfile>(profile);
  const [listDrafts, setListDrafts] = useState({
    avoid: profile.avoid.join(", "),
    skills: profile.skills.join(", "),
    targetRoles: profile.targetRoles.join(", "),
  });
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [savedProfile, setSavedProfile] = useState<CandidateProfile | null>(
    isProfileComplete(profile) ? profile : null,
  );

  useEffect(() => {
    setDraft(profile);
    setListDrafts({
      avoid: profile.avoid.join(", "),
      skills: profile.skills.join(", "),
      targetRoles: profile.targetRoles.join(", "),
    });
  }, [profile]);

  const normalizedDraft = useMemo(
    () => ({
      ...draft,
      avoid: parseList(listDrafts.avoid),
      skills: parseList(listDrafts.skills),
      targetRoles: parseList(listDrafts.targetRoles),
    }),
    [draft, listDrafts],
  );

  const missingFields = getMissingProfileFields(normalizedDraft);

  async function handleSave() {
    if (missingFields.length > 0) {
      setSaveState("invalid");
      return;
    }
    setSaveState("saving");
    try {
      const saved = await onSave(normalizedDraft);
      setDraft(saved);
      setListDrafts({
        avoid: saved.avoid.join(", "),
        skills: saved.skills.join(", "),
        targetRoles: saved.targetRoles.join(", "),
      });
      setSavedProfile(saved);
      setSaveState("saved");
    } catch {
      setSaveState("error");
    }
  }

  function updateDraft(updates: Partial<CandidateProfile>) {
    setSaveState((current) => (current === "saving" ? current : "idle"));
    setDraft((current) => ({ ...current, ...updates }));
  }

  function updateListDraft(key: keyof typeof listDrafts, value: string) {
    setSaveState((current) => (current === "saving" ? current : "idle"));
    setListDrafts((current) => ({ ...current, [key]: value }));
  }

  return (
    <section>
      <PageHead
        action={
          <button
            className="btn"
            disabled={saveState === "saving"}
            onClick={handleSave}
            type="button"
          >
            <Save size={15} />
            {saveState === "saving" ? "Saving" : "Save profile"}
          </button>
        }
        description={
          requiresSetup
            ? "Complete every required field before running discovery and scoring."
            : "Canonical information and search constraints the agent scores every job against. Companies or industries to avoid is optional."
        }
        title={requiresSetup ? "Set Up Your Profile" : "Candidate Profile"}
      />
      <div className="form-status-row">
        {saveState === "saved" && (
          <div className="notice success" role="status">
            <Check size={15} />
            Profile saved.
            <a href="#saved-profile-title">View saved profile</a>
          </div>
        )}
        {saveState === "invalid" && (
          <div className="notice warning" role="alert">
            Fill required fields: {missingFields.join(", ")}.
          </div>
        )}
        {saveState === "error" && (
          <div className="notice warning" role="alert">
            Profile could not be saved. Check the API connection and try again.
          </div>
        )}
      </div>
      <div className="profile-form-grid">
        <TextInput
          label="Full name"
          onChange={(value) => updateDraft({ fullName: value })}
          required
          value={draft.fullName}
        />
        <TextInput
          label="Email"
          onChange={(value) => updateDraft({ email: value })}
          required
          value={draft.email}
        />
        <TextInput
          label="Phone"
          onChange={(value) => updateDraft({ phone: value })}
          required
          value={draft.phone}
        />
        <TextInput
          label="Salary range"
          onChange={(value) => updateDraft({ salaryRange: value })}
          placeholder="$160,000 - $210,000"
          required
          value={draft.salaryRange}
        />
        <TextInput
          label="Location & remote"
          onChange={(value) => updateDraft({ locationPreference: value })}
          placeholder="Remote US, Bay Area hybrid"
          required
          value={draft.locationPreference}
        />
        <TextInput
          label="Work authorization"
          onChange={(value) => updateDraft({ workAuthorization: value })}
          placeholder="US Citizen, no sponsorship needed"
          required
          value={draft.workAuthorization}
        />
        <TextAreaInput
          help="Required. Separate roles with commas or new lines."
          label="Target roles"
          onChange={(value) => updateListDraft("targetRoles", value)}
          placeholder="Backend Engineer, ML Platform Engineer"
          required
          value={listDrafts.targetRoles}
        />
        <TextAreaInput
          help="Required. Separate skills with commas or new lines."
          label="Skills"
          onChange={(value) => updateListDraft("skills", value)}
          placeholder="Python, FastAPI, Postgres"
          required
          value={listDrafts.skills}
        />
        <TextAreaInput
          help="Optional. Separate companies or industries with commas or new lines."
          label="Companies or industries to avoid"
          onChange={(value) => updateListDraft("avoid", value)}
          placeholder="Ad-tech industry, specific company"
          value={listDrafts.avoid}
        />
      </div>
      {savedProfile && <SavedProfileSummary profile={savedProfile} />}
    </section>
  );
}

function SavedProfileSummary({ profile }: { profile: CandidateProfile }) {
  return (
    <section className="saved-profile" aria-labelledby="saved-profile-title">
      <div className="section-head">
        <h2 id="saved-profile-title">Saved Profile</h2>
        <span className="status-pill saved">Current saved version</span>
      </div>
      <div className="profile-grid">
        <ProfileField label="Full name" value={profile.fullName} />
        <ProfileField label="Email" value={profile.email} />
        <ProfileField label="Phone" value={profile.phone} />
        <ProfileField label="Salary range" value={profile.salaryRange} />
        <ProfileField
          label="Location & remote"
          value={profile.locationPreference}
        />
        <ProfileField
          label="Work authorization"
          value={profile.workAuthorization}
        />
        <ProfileField
          full
          label="Target roles"
          value={profile.targetRoles.join(", ")}
        />
        <ProfileField full label="Skills" value={profile.skills.join(", ")} />
        <ProfileField
          full
          label="Companies or industries to avoid"
          value={profile.avoid.length ? profile.avoid.join(", ") : "None"}
        />
      </div>
    </section>
  );
}

function ProfileField({
  label,
  value,
  full,
}: {
  label: string;
  value: string;
  full?: boolean;
}) {
  return (
    <div className={`field-card ${full ? "full" : ""}`}>
      <div className="field-label">{label}</div>
      <div className="field-value">{value || "-"}</div>
    </div>
  );
}

function PipelineScreen({
  data,
  onRefresh,
  onStatusChange,
  status,
}: {
  data: DashboardData;
  onRefresh: () => void;
  onStatusChange: (jobId: string, status: JobStatus) => void;
  status: DiscoveryStatus;
}) {
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const selectedJob =
    data.jobs.find((job) => job.id === selectedJobId) ?? null;

  function updateStatus(jobId: string, nextStatus: JobStatus) {
    onStatusChange(jobId, nextStatus);
  }

  return (
    <section>
      <PageHead
        action={
          <button
            className="btn ghost"
            disabled={status.state === "running"}
            onClick={onRefresh}
            type="button"
          >
            <RefreshCcw
              className={status.state === "running" ? "spin" : ""}
              size={15}
            />
            {status.state === "running" ? "Running discovery" : "Run discovery"}
          </button>
        }
        description="Every discovered job moves through this pipeline. Nothing is submitted without approval here."
        title="Pipeline"
      />
      <DiscoveryStatusNotice status={status} />
      <div className="pipeline-board">
        {pipelineStatuses.map((status) => {
          const jobs = data.jobs.filter((job) => job.status === status);
          return (
            <section className="pipe-col" key={status}>
              <div className="pipe-col-head">
                <span className="pipe-col-title">{statusLabel(status)}</span>
                <span className="pipe-col-count">{jobs.length}</span>
              </div>
              {jobs.length === 0 && (
                <div className="empty-card">No jobs in this stage.</div>
              )}
              {jobs.map((job) => (
                <article
                  className="job-card"
                  key={job.id}
                  onClick={() => setSelectedJobId(job.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedJobId(job.id);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <div>
                    <h3 className="job-title">{job.title}</h3>
                    <p className="job-company">{job.company}</p>
                  </div>
                  <div className="job-meta">
                    <span className="job-loc">{job.location}</span>
                    <span className={`score ${scoreTone(job.score)}`}>
                      {job.score ?? "-"}
                    </span>
                  </div>
                  {status === "needs_review" && (
                    <div
                      className="job-actions inline"
                      onClick={(event) => event.stopPropagation()}
                    >
                      <button
                        className="btn tiny"
                        onClick={() => updateStatus(job.id, "approved_to_apply")}
                        title="Approve to apply"
                        type="button"
                      >
                        <Check size={15} />
                        Approve
                      </button>
                      <button
                        className="btn tiny danger"
                        onClick={() => updateStatus(job.id, "rejected")}
                        title="Reject and remove from pipeline"
                        type="button"
                      >
                        <Trash2 size={15} />
                        Reject
                      </button>
                    </div>
                  )}
                </article>
              ))}
            </section>
          );
        })}
      </div>
      {selectedJob && (
        <JobDetailModal
          job={selectedJob}
          onClose={() => setSelectedJobId(null)}
          onStatusChange={updateStatus}
        />
      )}
    </section>
  );
}

function JobDetailModal({
  job,
  onClose,
  onStatusChange,
}: {
  job: DashboardData["jobs"][number];
  onClose: () => void;
  onStatusChange: (jobId: string, status: JobStatus) => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <section
        aria-labelledby="job-detail-title"
        aria-modal="true"
        className="job-modal"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className="modal-head">
          <div>
            <h2 id="job-detail-title">{job.title}</h2>
            <p>
              {job.company} · {job.location || "Location not listed"} ·{" "}
              {statusLabel(job.status)}
            </p>
          </div>
          <button className="icon-btn subtle" onClick={onClose} type="button">
            <X size={16} />
          </button>
        </div>
        <div className="modal-meta">
          <span className={`score ${scoreTone(job.score)}`}>
            Score {job.score ?? "-"}
          </span>
          {job.applicationUrl && (
            <a href={job.applicationUrl} rel="noreferrer" target="_blank">
              <ExternalLink size={14} />
              Open posting
            </a>
          )}
        </div>
        <div className="modal-actions">
          {job.status === "needs_review" && (
            <>
              <button
                className="btn"
                onClick={() => onStatusChange(job.id, "approved_to_apply")}
                type="button"
              >
                <Check size={15} />
                Approve to apply
              </button>
              <button
                className="btn danger"
                onClick={() => onStatusChange(job.id, "rejected")}
                type="button"
              >
                <Trash2 size={15} />
                Reject
              </button>
            </>
          )}
          {job.status === "approved_to_apply" && (
            <>
              <button
                className="btn"
                onClick={() => onStatusChange(job.id, "applied")}
                type="button"
              >
                <Send size={15} />
                Mark applied
              </button>
              <button
                className="btn ghost"
                onClick={() => onStatusChange(job.id, "needs_review")}
                type="button"
              >
                <ArrowLeft size={15} />
                Back to review
              </button>
              <button
                className="btn danger"
                onClick={() => onStatusChange(job.id, "rejected")}
                type="button"
              >
                <Trash2 size={15} />
                Reject
              </button>
            </>
          )}
          {job.status === "applied" && (
            <button
              className="btn"
              onClick={() => onStatusChange(job.id, "interviewing")}
              type="button"
            >
              <Check size={15} />
              Mark interviewing
            </button>
          )}
          {job.status === "interviewing" && (
            <>
              <button
                className="btn ghost"
                onClick={() => onStatusChange(job.id, "closed")}
                type="button"
              >
                Close
              </button>
              <button
                className="btn danger"
                onClick={() => onStatusChange(job.id, "rejected")}
                type="button"
              >
                <Trash2 size={15} />
                Reject
              </button>
            </>
          )}
        </div>
        <div className="job-description">
          <h3>Job Description</h3>
          <p>{job.content || "No job description was stored for this posting."}</p>
        </div>
      </section>
    </div>
  );
}

function DiscoveryStatusNotice({ status }: { status: DiscoveryStatus }) {
  const tone =
    status.state === "completed"
      ? "success"
      : status.state === "local" || status.state === "error"
        ? "warning"
        : "info";
  return (
    <div className={`discovery-status notice ${tone}`} role="status">
      <RefreshCcw
        className={status.state === "running" ? "spin" : ""}
        size={15}
      />
      <div>
        <div>{status.message}</div>
        {status.checkedAt && (
          <small>
            Last checked at {status.checkedAt}. If a backend run is still
            processing, check back in about a minute and refresh discovery again.
          </small>
        )}
      </div>
    </div>
  );
}

function DuplicatesScreen({ data }: { data: DashboardData }) {
  return (
    <section>
      <PageHead
        description="Jobs flagged by fingerprint or canonical URL match. Review before either is scored further."
        title="Possible Duplicates"
      />
      {data.duplicates.length === 0 && (
        <EmptyState text="No possible duplicates have been flagged." />
      )}
      {data.duplicates.map((duplicate) => (
        <article className="dup-pair" key={duplicate.id}>
          <div className="dup-head">
            <span className="dup-flag">{duplicate.reason}</span>
            <span className="cell-mono">
              confidence {duplicate.confidence.toFixed(2)}
            </span>
          </div>
          <div className="dup-body">
            {duplicate.jobs.map((job) => (
              <div className="dup-item" key={job.identity}>
                <div className="dup-item-label">{job.label}</div>
                <div className="dup-title">{job.title}</div>
                <div className="dup-sub">
                  {job.company} · {job.location} · {job.discoveredAt}
                </div>
                <div className="cell-mono">{job.identity}</div>
              </div>
            ))}
          </div>
          <div className="dup-actions">
            <button className="btn small" type="button">
              <CopyCheck size={14} />
              Merge duplicate
            </button>
            <button className="btn small ghost" type="button">
              Keep both
            </button>
          </div>
        </article>
      ))}
    </section>
  );
}

function LedgerScreen({
  entries,
  search,
  selectedStatus,
  onSearchChange,
  onStatusChange,
}: {
  entries: DashboardData["ledger"];
  search: string;
  selectedStatus: JobStatus | "all";
  onSearchChange: (value: string) => void;
  onStatusChange: (value: JobStatus | "all") => void;
}) {
  return (
    <section>
      <PageHead
        description="Full history of every job seen and every application submitted."
        title="Application Ledger"
      />
      <div className="toolbar">
        <label className="search-field">
          <Search size={15} />
          <input
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Search company or title"
            type="text"
            value={search}
          />
        </label>
        <select
          onChange={(event) =>
            onStatusChange(event.target.value as JobStatus | "all")
          }
          value={selectedStatus}
        >
          <option value="all">All statuses</option>
          <option value="applied">Applied</option>
          <option value="interviewing">Interviewing</option>
          <option value="rejected">Rejected</option>
          <option value="needs_review">Needs review</option>
          <option value="approved_to_apply">Approved to apply</option>
        </select>
      </div>
      {entries.length === 0 ? (
        <EmptyState text="No ledger entries match the current filters." />
      ) : (
        <table>
          <thead>
            <tr>
              <th>Company</th>
              <th>Title</th>
              <th>Status</th>
              <th>Applied</th>
              <th>Resume ver.</th>
              <th>Confirmation</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id}>
                <td>{entry.company}</td>
                <td>{entry.title}</td>
                <td>
                  <span className={`status-pill ${statusTone(entry.status)}`}>
                    {entry.status}
                  </span>
                </td>
                <td className="cell-mono">{entry.appliedAt ?? "-"}</td>
                <td className="cell-mono">{entry.resumeVersion ?? "-"}</td>
                <td className="cell-mono">{entry.confirmation ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function PageHead({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        <h1 className="page-title">{title}</h1>
        <p className="page-desc">{description}</p>
      </div>
      {action}
    </div>
  );
}

function TextInput({
  label,
  value,
  onChange,
  placeholder,
  required,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  required?: boolean;
}) {
  return (
    <label className="form-field">
      <span>
        {label}
        <em>{required ? "Required" : "Optional"}</em>
      </span>
      <input
        aria-required={required}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        type="text"
        value={value}
      />
    </label>
  );
}

function TextAreaInput({
  label,
  value,
  onChange,
  placeholder,
  help,
  required,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  help?: string;
  required?: boolean;
}) {
  return (
    <label className="form-field full">
      <span>
        {label}
        <em>{required ? "Required" : "Optional"}</em>
      </span>
      <textarea
        aria-required={required}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        value={value}
      />
      {help && <small>{help}</small>}
    </label>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>;
}

function statusLabel(status: JobStatus) {
  return status.replaceAll("_", " ").toUpperCase();
}

function scoreTone(score: number | null) {
  if (score === null) {
    return "low";
  }
  if (score >= 80) {
    return "high";
  }
  if (score >= 60) {
    return "mid";
  }
  return "low";
}

function statusTone(status: JobStatus) {
  if (status === "applied") {
    return "applied";
  }
  if (status === "interviewing") {
    return "interviewing";
  }
  if (status === "rejected") {
    return "rejected";
  }
  if (status === "needs_review") {
    return "review";
  }
  return "saved";
}

function formatTime(value: Date) {
  return value.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

function parseList(value: string) {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function getMissingProfileFields(profile: CandidateProfile) {
  const fields: Array<[string, boolean]> = [
    ["full name", Boolean(profile.fullName.trim())],
    ["email", Boolean(profile.email.trim())],
    ["phone", Boolean(profile.phone.trim())],
    ["salary range", Boolean(profile.salaryRange.trim())],
    ["location & remote", Boolean(profile.locationPreference.trim())],
    ["work authorization", Boolean(profile.workAuthorization.trim())],
    ["target roles", profile.targetRoles.length > 0],
    ["skills", profile.skills.length > 0],
  ];
  return fields.filter(([, present]) => !present).map(([label]) => label);
}

function decodeGoogleCredential(credential: string): {
  email: string;
  name: string;
  picture?: string;
} {
  const [, payload] = credential.split(".");
  const decoded = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as {
    email?: string;
    name?: string;
    picture?: string;
  };
  return {
    email: decoded.email ?? "",
    name: decoded.name ?? decoded.email ?? "",
    picture: decoded.picture,
  };
}

const rootElement = document.getElementById("root")!;
const root = window.jobAgentRoot ?? createRoot(rootElement);
window.jobAgentRoot = root;

root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
