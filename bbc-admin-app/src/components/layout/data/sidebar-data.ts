import {
  BookOpen,
  CircleDot,
  KeyRound,
  LayoutGrid,
  MessageCircle,
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
  | 'settings'

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
 *   - owner/admin/dev: Conversations, Tasks, Dashboard, Leads | Team, Users & access, Assistant | Settings
 *   - QA: Conversations, Dashboard, Leads | Assistant | Settings
 *   - supervisor / PM: Conversations, Tasks, Dashboard | Team | Settings
 * Assistant (the knowledge base editor) is the existing page, not redesigned yet.
 */
export function getNavGroups(
  role: UserRole,
  permissions: Permissions
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
