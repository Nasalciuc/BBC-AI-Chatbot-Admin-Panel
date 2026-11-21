import {
  BookOpen,
  ClipboardCheck,
  CircleDot,
  GraduationCap,
  KeyRound,
  LayoutGrid,
  MessageCircle,
  Radio,
  ShieldAlert,
  SlidersHorizontal,
  SquareCheck,
  Users,
} from 'lucide-react'
import type { Permissions, UserRole } from '@/lib/bbc/types'
import { canSeeTeam } from '@/features/teams/view'
import { type SidebarData } from '../types'

export type NavKey =
  | 'conv'
  | 'tasks'
  | 'dash'
  | 'leads'
  | 'team'
  | 'users'
  | 'assist'
  | 'learning'
  | 'bans'
  | 'settings'
  | 'live'
  | 'qa'

export type PanelNavItem = {
  key: NavKey
  title: string
  url: string
  icon: React.ElementType
}

/** Operators: the people who take chats (sales, support). */
export function isOperatorRole(role: string | undefined): boolean {
  return role === 'sales' || role === 'support'
}

/**
 * The panel menu, per role — the design canvas's menu ("what we can build
 * now"), restricted to pages that exist in this build:
 *   - agents: Conversations, Tasks, Dashboard (Your day) | Knowledge | Settings
 *   - owner/admin/dev: Conversations, Tasks, Dashboard, Leads | Team, Users & access, Assistant, Learning, Security · Bans | Settings
 *   - QA: Conversations, Dashboard, Leads | Assistant | Settings
 *   - supervisor / PM: Conversations, Tasks, Dashboard | Team | Settings
 * Assistant (the knowledge base editor) is the existing page, not redesigned yet.
 * Learning (v2 PR-8, the question variants) is v2's page, also not redesigned;
 * it shows for the roles the backend lets in (CAN_REVIEW_LESSONS =
 * owner/admin/dev), so a project manager never lands on a page of 403s.
 * Security · Bans (v2 PR-11, the ban list and Unban) likewise: v2's page, for
 * the backend's MANAGEMENT set (app/security/gates.py) — owner/admin/dev.
 */
export function getNavGroups(
  role: UserRole,
  permissions: Permissions,
  // "On the site now": only while the heartbeat hands this person a pass to bbc-live (the switch is on).
  extras: { live?: boolean } = {}
): PanelNavItem[][] {
  const op = isOperatorRole(role)
  const g1: PanelNavItem[] = [
    { key: 'conv', title: 'Conversations', url: '/chats', icon: MessageCircle },
    // Tasks: what each person promised clients (QA has none to follow up).
    ...(role !== 'qa'
      ? [{ key: 'tasks' as const, title: 'Tasks', url: '/tasks', icon: SquareCheck }]
      : []),
    { key: 'dash', title: 'Dashboard', url: '/', icon: LayoutGrid },
    ...(permissions.canViewLeads
      ? [{ key: 'leads' as const, title: 'Leads', url: '/leads', icon: CircleDot }]
      : []),
    ...(extras.live
      ? [{ key: 'live' as const, title: 'On the site now', url: '/live', icon: Radio }]
      : []),
    // The morning QA sample: QA and management only (app/api/qa.py READERS).
    ...(['qa', 'owner', 'admin', 'dev'].includes(role)
      ? [{ key: 'qa' as const, title: 'QA sample', url: '/qa', icon: ClipboardCheck }]
      : []),
  ]
  const g2: PanelNavItem[] = [
    ...(canSeeTeam(role)
      ? [{ key: 'team' as const, title: 'Team', url: '/teams', icon: Users }]
      : []),
    ...(permissions.canViewUsers
      ? [{ key: 'users' as const, title: 'Users & access', url: '/users', icon: KeyRound }]
      : []),
    ...(op
      ? [{ key: 'assist' as const, title: 'Knowledge', url: '/knowledge', icon: BookOpen }]
      : permissions.canViewKB
        ? [{ key: 'assist' as const, title: 'Assistant', url: '/knowledge-base', icon: BookOpen }]
        : []),
    ...(permissions.canReviewLearning
      ? [{ key: 'learning' as const, title: 'Learning', url: '/learning', icon: GraduationCap }]
      : []),
    ...(permissions.canManageBans
      ? [{ key: 'bans' as const, title: 'Security · Bans', url: '/security/bans', icon: ShieldAlert }]
      : []),
  ]
  return [g1, g2].filter((g) => g.length > 0)
}

export const SETTINGS_ITEM: PanelNavItem = {
  key: 'settings',
  title: 'Settings',
  url: '/settings',
  icon: SlidersHorizontal,
}

/** Shape kept for the command menu (search). */
export function getSidebarData(
  permissions: Permissions,
  userName: string,
  userEmail: string,
  role: UserRole = 'sales'
): SidebarData {
  const groups = getNavGroups(role, permissions)
  return {
    user: { name: userName || 'User', email: userEmail || '', avatar: '' },
    teams: [],
    navGroups: [
      ...groups.map((items, i) => ({
        title: i === 0 ? 'Panel' : 'More',
        items: items.map(({ title, url, icon }) => ({ title, url, icon })),
      })),
      {
        title: 'You',
        items: [{ title: SETTINGS_ITEM.title, url: SETTINGS_ITEM.url, icon: SETTINGS_ITEM.icon }],
      },
    ],
  }
}
