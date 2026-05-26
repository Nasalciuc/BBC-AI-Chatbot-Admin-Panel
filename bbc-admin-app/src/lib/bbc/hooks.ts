// ── BBC RBAC Hooks ───────────────────────────────────────────────
// V1: mock auth with role switcher. V2: swap to Supabase Auth.

import { useMemo } from 'react'
import type { UserRole, Permissions } from './types'

function getPermissions(role: UserRole): Permissions {
  switch (role) {
    case 'owner':
    case 'admin':
    case 'dev':
      return {
        canViewLeads: true, canEditLeads: true, canViewAllConversations: true,
        canReadMessages: true, canReassignConversations: true,
        canViewUsers: true, canEditUsers: true, canEditKB: true, canViewKB: true,
        canProposeKBChanges: true, canViewIntegrations: true, canEditSettings: true,
        canViewAllSettings: true, canViewDashboardGlobal: true, canAssignTasks: true,
        visibleTunnels: ['all'],
      }
    case 'qa':
      return {
        canViewLeads: true, canEditLeads: false, canViewAllConversations: true,
        canReadMessages: true, canReassignConversations: false,
        canViewUsers: false, canEditUsers: false, canEditKB: false, canViewKB: true,
        canProposeKBChanges: false, canViewIntegrations: false, canEditSettings: false,
        canViewAllSettings: false, canViewDashboardGlobal: true, canAssignTasks: false,
        visibleTunnels: ['all'],
      }
    case 'sales':
      return {
        canViewLeads: true, canEditLeads: false, canViewAllConversations: false,
        canReadMessages: true, canReassignConversations: false,
        canViewUsers: false, canEditUsers: false, canEditKB: false, canViewKB: true,
        canProposeKBChanges: true, canViewIntegrations: false, canEditSettings: false,
        canViewAllSettings: false, canViewDashboardGlobal: false, canAssignTasks: false,
        visibleTunnels: ['sales'],
      }
    case 'support':
      return {
        canViewLeads: false, canEditLeads: false, canViewAllConversations: false,
        canReadMessages: true, canReassignConversations: false,
        canViewUsers: false, canEditUsers: false, canEditKB: false, canViewKB: true,
        canProposeKBChanges: true, canViewIntegrations: false, canEditSettings: false,
        canViewAllSettings: false, canViewDashboardGlobal: false, canAssignTasks: false,
        visibleTunnels: ['support'],
      }
    case 'supervisor':
      return {
        canViewLeads: false, canEditLeads: false, canViewAllConversations: true,
        canReadMessages: false, canReassignConversations: true,
        canViewUsers: false, canEditUsers: false, canEditKB: false, canViewKB: false,
        canProposeKBChanges: false, canViewIntegrations: false, canEditSettings: false,
        canViewAllSettings: false, canViewDashboardGlobal: false, canAssignTasks: false,
        visibleTunnels: ['all'],
      }
    default:
      return {
        canViewLeads: false, canEditLeads: false, canViewAllConversations: false,
        canReadMessages: true, canReassignConversations: false,
        canViewUsers: false, canEditUsers: false, canEditKB: false, canViewKB: true,
        canProposeKBChanges: false, canViewIntegrations: false, canEditSettings: false,
        canViewAllSettings: false, canViewDashboardGlobal: false, canAssignTasks: false,
        visibleTunnels: ['sales'],
      }
  }
}

export function usePermissions(role: UserRole): Permissions {
  return useMemo(() => getPermissions(role), [role])
}
