<template>
  <n-config-provider :theme="naiveTheme" :theme-overrides="themeOverrides" :locale="ruRU" :date-locale="dateRuRU">
    <n-dialog-provider>
      <n-message-provider>
        <RouterView v-slot="{ Component }">
          <keep-alive :include="cachedViews">
            <component :is="Component" />
          </keep-alive>
        </RouterView>
        <PanelUpdateOverlay />
      </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  darkTheme,
  dateRuRU,
  lightTheme,
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  ruRU
} from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'

import PanelUpdateOverlay from '@/components/PanelUpdateOverlay.vue'
import { useThemeStore } from '@/stores/theme'

const themeStore = useThemeStore()
themeStore.initFromUser()

// Страницы-списки держим в памяти: при возврате показываем данные мгновенно,
// а сами компоненты в фоне сверяются с сервером (см. onRevisit в каждой view).
// Детальные страницы, чат (таймеры) и логин намеренно НЕ кэшируем.
const cachedViews = [
  'DashboardView',
  'ServersView',
  'ClientsView',
  'InvoicesView',
  'UsersView',
  'ChannelsView',
  'SettingsView'
]

const naiveTheme = computed(() => (themeStore.mode === 'dark' ? darkTheme : lightTheme))

const themeOverrides = computed<GlobalThemeOverrides>(() => {
  const dark = themeStore.mode === 'dark'
  return {
    common: {
      primaryColor: '#6FBF9A',
      primaryColorHover: '#85D2AE',
      primaryColorPressed: '#4E9F7C',
      primaryColorSuppl: '#6FBF9A',
      bodyColor: dark ? '#101214' : '#e8ebe6',
      baseColor: dark ? '#171A1D' : '#f4f6f2',
      cardColor: dark ? '#171A1D' : '#f4f6f2',
      modalColor: dark ? '#171A1D' : '#f4f6f2',
      popoverColor: dark ? '#202429' : '#ecefe9',
      borderColor: dark ? '#2C3230' : '#c5cbc3',
      textColorBase: dark ? '#F2F3F0' : '#1a1d1a',
      textColor1: dark ? '#F2F3F0' : '#1a1d1a',
      textColor2: dark ? '#B8BDB7' : '#4f564f',
      textColor3: dark ? '#7D8580' : '#6d746c',
      borderRadius: '8px',
      borderRadiusSmall: '6px',
      fontFamily: 'Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      fontFamilyMono: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
    },
    Button: {
      borderRadiusMedium: '7px',
      heightMedium: '36px',
      textColorPrimary: dark ? '#101214' : '#f4f6f2'
    },
    Input: {
      borderRadius: '7px',
      color: dark ? '#101214' : '#f4f6f2',
      colorFocus: dark ? '#101214' : '#f4f6f2',
      borderColor: dark ? '#2C3230' : '#c5cbc3',
      borderColorFocus: '#6FBF9A'
    },
    Table: {
      thColor: dark ? '#171A1D' : '#ecefe9',
      tdColor: dark ? '#171A1D' : '#f4f6f2',
      tdColorHover: dark ? '#202429' : '#ecefe9',
      borderColor: dark ? '#2C3230' : '#c5cbc3'
    }
  }
})
</script>
