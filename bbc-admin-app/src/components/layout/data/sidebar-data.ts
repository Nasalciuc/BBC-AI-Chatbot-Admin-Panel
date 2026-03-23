import {
  LayoutDashboard, MessageSquare, Users, UserPlus, BookOpen,
  Settings, UserCog, Wrench, Palette, Bell, Monitor,
} from 'lucide-react'
import { Logo } from '@/assets/logo'
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
    teams: [
      { name: 'BuyBusinessClass', logo: Logo, plan: 'Admin Panel' },
    ],
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
      ...(permissions.canViewUsers || permissions.canViewKB
        ? [{
            title: 'Management',
            items: [
              ...(permissions.canViewUsers
                ? [{ title: 'Users', url: '/users' as const, icon: Users }]
                : []),
              ...(permissions.canViewKB
                ? [{ title: 'Knowledge Base', url: '/knowledge-base' as const, icon: BookOpen }]
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
