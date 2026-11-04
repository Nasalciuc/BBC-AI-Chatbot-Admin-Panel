import {
  LayoutDashboard, MessageSquare, Users, UserPlus, BookOpen,
  Settings, UserCog, Wrench, Palette, Bell, Monitor, ListChecks, UsersRound, GraduationCap, ShieldAlert,
} from 'lucide-react'
import { type SidebarData } from '../types'
import type { Permissions } from '@/lib/bbc/types'

export function getSidebarData(
  permissions: Permissions,
  userName: string,
  userEmail: string,
): SidebarData {
  return {
    user: {
      name: userName || 'User',
      email: userEmail || '',
      avatar: '/avatars/01.png',
    },
    // Deprecated: the header no longer uses a hardcoded team. TeamSwitcher now
    // loads real teams from the API (see components/layout/team-switcher.tsx).
    // Kept empty to satisfy the SidebarData type without a hardcoded brand entry.
    teams: [],
    navGroups: [
      {
        title: 'Main',
        items: [
          { title: 'Dashboard', url: '/', icon: LayoutDashboard },
          { title: 'Conversations', url: '/chats', icon: MessageSquare },
          ...(permissions.canViewLeads
            ? [{ title: 'Leads', url: '/leads' as const, icon: UserPlus }]
            : []),
        ],
      },
      ...(permissions.canViewUsers || permissions.canViewKB || permissions.canAssignTasks || permissions.canManageTeams
        || permissions.canReviewLearning
        ? [{
            title: 'Management',
            items: [
              ...(permissions.canViewUsers
                ? [{ title: 'Users', url: '/users' as const, icon: Users }]
                : []),
              // Management-only page (create/edit teams, assign people). PM/supervisor
              // read-only view can be added later; gated on canManageTeams for now.
              ...(permissions.canManageTeams
                ? [{ title: 'Teams', url: '/teams' as const, icon: UsersRound }]
                : []),
              ...(permissions.canViewKB
                ? [{ title: 'Knowledge Base', url: '/knowledge-base' as const, icon: BookOpen }]
                : []),
              ...(permissions.canAssignTasks
                ? [{ title: 'Tasks', url: '/tasks' as const, icon: ListChecks }]
                : []),
              // The learning loop's human gate — owner/admin/dev only, the same
              // set the backend allows (CAN_REVIEW_LESSONS). Not canManageTeams:
              // a project manager has that and would land on a page of 403s.
              ...(permissions.canReviewLearning
                ? [{ title: 'Learning', url: '/learning' as const, icon: GraduationCap }]
                : []),
              ...(permissions.canManageTeams
                ? [{ title: 'Security · Bans', url: '/security/bans' as const, icon: ShieldAlert }]
                : []),
            ],
          }]
        : []),
      {
        title: 'System',
        items: [
          {
            title: 'Settings', icon: Settings,
            items: [
              { title: 'Profile', url: '/settings' as const, icon: UserCog },
              { title: 'Appearance', url: '/settings/appearance' as const, icon: Palette },
              ...(permissions.canEditSettings
                ? [
                    { title: 'Account', url: '/settings/account' as const, icon: Wrench },
                    { title: 'Notifications', url: '/settings/notifications' as const, icon: Bell },
                    { title: 'Display', url: '/settings/display' as const, icon: Monitor },
                  ]
                : []),
            ],
          },
        ],
      },
    ],
  }
}
