// The second sign-in step's own check, before it asks the server.
// Pure — pinned by tests/mfa-sign-in.test.ts.

/** The server takes 6 characters at least: a 6-digit code, or a recovery
 *  code. Shorter comes back as a 422 whose detail is a list, which the
 *  sign-in can only show as "Something went wrong" (and it cleared the field). */
export const MFA_CODE_MIN = 6

/** What to say under the field, or null when the code can go to the server. */
export function mfaCodeProblem(code: string, mode: 'required' | 'enroll'): string | null {
  if (code.replace(/\s/g, '').length >= MFA_CODE_MIN) return null
  return mode === 'enroll'
    ? 'Enter the 6 digits your authenticator app shows.'
    : 'Enter the 6-digit code from your authenticator app, or one of your recovery codes.'
}
