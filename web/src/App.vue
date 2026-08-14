<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const showTabs = computed(() => !route.meta?.fullscreen)
</script>

<template>
  <div class="app">
    <main class="page">
      <router-view />
    </main>
    <nav v-if="showTabs" class="tabbar">
      <router-link to="/" class="tab" :class="{ active: route.path === '/' }">
        <span class="tab-ico">▦</span>
        <span>流水线</span>
      </router-link>
      <router-link to="/runs" class="tab" :class="{ active: route.path === '/runs' }">
        <span class="tab-ico">▶</span>
        <span>运行</span>
      </router-link>
      <router-link to="/settings" class="tab" :class="{ active: route.path === '/settings' }">
        <span class="tab-ico">⚙</span>
        <span>设置</span>
      </router-link>
    </nav>
  </div>
</template>
