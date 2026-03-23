import {
  LayoutDashboard, MessageSquare, Users, UserPlus, BookOpen,
  Settings, UserCog, Wrench, Palette, Bell, Monitor,
} from 'lucide-react'
import { Logo } from '@/assets/logo'
import { type SidebarData } from '../types'

export const sidebarData: SidebarData = {
  user: {
    name: 'Scaler',
    email: 'scaler@buybusinessclass.com',
    avatar: '/avatars/shadcn.jpg',
  },
  teams: [
    { name: 'BuyBusinessClass', logo: Logo, plan: 'Admin Panel' },
  ],
  navGroups: [
    {
      title: 'Main',
      items: [
        { title: 'Dashboard', url: '/', icon: LayoutDashboard },
        { title: 'Conversations', url: '/chats', badge: '3', icon: MessageSquare },
        // { title: 'Tasks', url: '/tasks', icon: ListTodo },  // V2: no backend
        { title: 'Leads', url: '/leads', icon: UserPlus },
      ],
    },
    {
      title: 'Management',
      items: [
        { title: 'Users', url: '/users', icon: Users },
        { title: 'Knowledge Base', url: '/knowledge-base', icon: BookOpen },
      ],
    },
    {
      title: 'System',
      items: [
        // { title: 'Integrations', url: '/apps', icon: Puzzle },  // V2: no backend
        {
          title: 'Settings', icon: Settings,
          items: [
            { title: 'Profile', url: '/settings', icon: UserCog },
            { title: 'Account', url: '/settings/account', icon: Wrench },
            { title: 'Appearance', url: '/settings/appearance', icon: Palette },
            { title: 'Notifications', url: '/settings/notifications', icon: Bell },
            { title: 'Display', url: '/settings/display', icon: Monitor },
          ],
        },
      ],
    },
  ],
}
