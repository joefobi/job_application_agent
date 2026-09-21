export type JobStatus =
  | "discovered"
  | "duplicate_possible"
  | "needs_review"
  | "approved_to_apply"
  | "started_application"
  | "applied"
  | "interviewing"
  | "rejected"
  | "saved"
  | "closed"
  | "failed"
  | "withdrawn";

export interface UserSession {
  idToken: string;
  email: string;
  name: string;
  picture?: string;
}

export interface CandidateProfile {
  fullName: string;
  email: string;
  phone: string;
  targetRoles: string[];
  locationPreference: string;
  salaryRange: string;
  workAuthorization: string;
  skills: string[];
  avoid: string[];
}

export interface DashboardData {
  jobs: Array<{
    applicationUrl?: string;
    content?: string | null;
    id: string;
    title: string;
    company: string;
    location: string;
    status: JobStatus;
    score: number | null;
  }>;
  duplicates: Array<{
    id: string;
    reason: string;
    confidence: number;
    jobs: Array<{
      label: string;
      title: string;
      company: string;
      location: string;
      discoveredAt: string;
      identity: string;
    }>;
  }>;
  ledger: Array<{
    id: string;
    company: string;
    title: string;
    status: JobStatus;
    appliedAt?: string;
    resumeVersion?: string;
    confirmation?: string;
  }>;
}

export interface ApplicationPreparation {
  jobId: string;
  status: JobStatus;
  requiresUserApproval: boolean;
  materials: {
    resumeVersion?: string | null;
    coverLetterVersion: string;
    coverLetterText: string;
    shortAnswers: Record<string, string>;
    requiresReview: boolean;
  };
  form: {
    provider: string;
    applicationUrl: string;
    stopBeforeSubmit: boolean;
    readyForUserReview: boolean;
    fields: Array<{
      fieldKey: string;
      action: "fill" | "upload" | "review";
      value?: string | null;
      requiresReview: boolean;
    }>;
  };
}

export interface ApiResult<T> {
  data: T;
  fromApi: boolean;
}

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

const SESSION_KEY = "job-agent.session";
const PROFILE_KEY = "job-agent.profile";

export function loadStoredSession(): UserSession | null {
  return readLocalJson<UserSession>(SESSION_KEY);
}

export function storeSession(session: UserSession): void {
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function clearSession(): void {
  localStorage.removeItem(SESSION_KEY);
}

export function emptyProfile(session: UserSession): CandidateProfile {
  return {
    fullName: session.name,
    email: session.email,
    phone: "",
    targetRoles: [],
    locationPreference: "",
    salaryRange: "",
    workAuthorization: "",
    skills: [],
    avoid: [],
  };
}

export function isProfileComplete(profile: CandidateProfile): boolean {
  return Boolean(
    profile.fullName.trim() &&
      profile.email.trim() &&
      profile.phone.trim() &&
      profile.targetRoles.length &&
      profile.locationPreference.trim() &&
      profile.salaryRange.trim() &&
      profile.workAuthorization.trim() &&
      profile.skills.length,
  );
}

export async function fetchProfile(
  session: UserSession,
): Promise<ApiResult<CandidateProfile | null>> {
  try {
    const data = await request<CandidateProfile>("/api/profile", session);
    return { data, fromApi: true };
  } catch {
    return { data: readLocalJson<CandidateProfile>(PROFILE_KEY), fromApi: false };
  }
}

export async function saveProfile(
  profile: CandidateProfile,
  session: UserSession,
): Promise<ApiResult<CandidateProfile>> {
  try {
    const data = await request<CandidateProfile>("/api/profile", session, {
      body: JSON.stringify(profile),
      headers: { "Content-Type": "application/json" },
      method: "PUT",
    });
    localStorage.setItem(PROFILE_KEY, JSON.stringify(data));
    return { data, fromApi: true };
  } catch {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
    return { data: profile, fromApi: false };
  }
}

export async function fetchDashboardData(
  session: UserSession,
): Promise<ApiResult<DashboardData>> {
  try {
    const data = await request<DashboardData>("/api/dashboard", session);
    return { data, fromApi: true };
  } catch {
    return { data: emptyDashboardData, fromApi: false };
  }
}

export async function runDiscovery(session: UserSession): Promise<void> {
  await request<void>("/api/discovery/runs", session, {
    method: "POST",
  });
}

export async function updateJobStatus(
  jobId: string,
  status: JobStatus,
  session: UserSession,
): Promise<void> {
  await request<void>(`/api/jobs/${jobId}/status`, session, {
    body: JSON.stringify({ status }),
    headers: { "Content-Type": "application/json" },
    method: "PATCH",
  });
}

export async function prepareApplication(
  jobId: string,
  session: UserSession,
): Promise<ApplicationPreparation> {
  return request<ApplicationPreparation>(
    `/api/jobs/${jobId}/application-run`,
    session,
    {
      method: "POST",
    },
  );
}

export async function confirmSubmission(
  jobId: string,
  session: UserSession,
): Promise<void> {
  await request<void>(`/api/jobs/${jobId}/submission-confirmations`, session, {
    body: JSON.stringify({
      notes: "Confirmed from the local dashboard after user review.",
    }),
    headers: { "Content-Type": "application/json" },
    method: "POST",
  });
}

async function request<T>(
  path: string,
  session: UserSession,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${session.idToken}`);
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function readLocalJson<T>(key: string): T | null {
  const value = localStorage.getItem(key);
  if (!value) {
    return null;
  }
  try {
    return JSON.parse(value) as T;
  } catch {
    return null;
  }
}

export const emptyDashboardData: DashboardData = {
  jobs: [],
  duplicates: [],
  ledger: [],
};
