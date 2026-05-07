<script lang="ts">
	import { WEBUI_VERSION } from '$lib/constants';
	import { WEBUI_NAME } from '$lib/stores';
	import { getContext } from 'svelte';

	const i18n = getContext('i18n');
</script>

<div class="flex flex-col h-full justify-between space-y-3 text-sm mb-6">
	<div class=" space-y-3 overflow-y-scroll max-h-[28rem] lg:max-h-full">
		<div>
			<div class=" mb-2.5 text-sm font-medium flex space-x-2 items-center">
				<div>
					{$WEBUI_NAME}
					{$i18n.t('Version')}
				</div>
			</div>
			<div class="flex w-full items-center">
				<div class="text-xs text-gray-700 dark:text-gray-200">
					v{WEBUI_VERSION}
				</div>
			</div>
		</div>
	</div>
</div>
