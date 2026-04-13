<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import Folder from '../../icons/Folder.svelte';

	type TreeNode = {
		name: string;
		path: string;
		type: 'file' | 'directory';
		size?: number;
		modified?: number;
	};

	const i18n = getContext('i18n') as any;
	const dispatch = createEventDispatcher();

	export let node: TreeNode;
	export let depth = 0;
	export let currentPath = '';
	export let treeNodes: Record<string, TreeNode[]> = {};
	export let expandedPaths: Set<string> = new Set();
	export let loadingPaths: Set<string> = new Set();
	export let errorPaths: Record<string, string> = {};

	$: isDirectory = node.type === 'directory';
	$: isExpanded = isDirectory && expandedPaths.has(node.path);
	$: isLoading = isDirectory && loadingPaths.has(node.path);
	$: children = isDirectory ? (treeNodes[node.path] ?? []) : [];
	$: isActiveBranch = isDirectory && currentPath.startsWith(node.path);

	const paddingLeft = (level: number) => `${level * 0.75}rem`;

	const toggleDirectory = (event: MouseEvent) => {
		event.stopPropagation();
		dispatch('toggle', { path: node.path });
	};

	const handlePrimaryClick = () => {
		if (isDirectory) {
			dispatch('navigate', { path: node.path });
			return;
		}

		dispatch('download', { path: node.path });
	};

	const handleDownload = (event: MouseEvent) => {
		event.stopPropagation();
		dispatch('download', { path: node.path });
	};
</script>

<li>
	<div
		class="group flex items-center gap-1 rounded-lg pr-1 {isActiveBranch
			? 'bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-200'
			: 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800/70'}"
		style:padding-left={paddingLeft(depth)}
	>
		{#if isDirectory}
			<button
				type="button"
				class="shrink-0 rounded p-1 text-gray-400 transition hover:bg-gray-200 hover:text-gray-700 dark:text-gray-500 dark:hover:bg-gray-700 dark:hover:text-gray-200"
				on:click={toggleDirectory}
				aria-label={isExpanded ? $i18n.t('Collapse') : $i18n.t('Expand')}
			>
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 20 20"
					fill="currentColor"
					class="size-3.5 transition-transform {isExpanded ? 'rotate-90' : ''}"
				>
					<path
						fill-rule="evenodd"
						d="M7.22 5.22a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 1 1-1.06-1.06L10.94 10 7.22 6.28a.75.75 0 0 1 0-1.06Z"
						clip-rule="evenodd"
					/>
				</svg>
			</button>
		{/if}

		<button
			type="button"
			class="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 py-1 text-left text-sm"
			on:click={handlePrimaryClick}
		>
			{#if isDirectory}
				<Folder className="size-4 shrink-0 text-blue-400 dark:text-blue-300" />
			{:else}
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="1.5"
					class="size-4 shrink-0 text-gray-400"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
					/>
				</svg>
			{/if}
			<span class="truncate">{node.name}</span>
		</button>

		{#if !isDirectory}
			<button
				type="button"
				class="shrink-0 rounded p-1 text-gray-400 opacity-0 transition hover:bg-gray-200 hover:text-gray-700 group-hover:opacity-100 dark:text-gray-500 dark:hover:bg-gray-700 dark:hover:text-gray-200"
				on:click={handleDownload}
				aria-label={$i18n.t('Download')}
			>
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="1.5"
					class="size-3.5"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						d="M12 3v12m0 0 4.5-4.5M12 15l-4.5-4.5M4.5 16.5v1.125A2.625 2.625 0 0 0 7.125 20.25h9.75A2.625 2.625 0 0 0 19.5 17.625V16.5"
					/>
				</svg>
			</button>
		{/if}
	</div>

	{#if isDirectory && isExpanded}
		{#if isLoading}
			<div class="py-1 pl-[2.5rem] text-xs text-gray-400 dark:text-gray-500">{$i18n.t('Loading...')}</div>
		{:else if errorPaths[node.path]}
			<div class="py-1 pl-[2.5rem] text-xs text-red-500 dark:text-red-400">{errorPaths[node.path]}</div>
		{:else if children.length === 0}
			<div class="py-1 pl-[2.5rem] text-xs text-gray-400 dark:text-gray-500">{$i18n.t('Empty folder')}</div>
		{:else}
			<ul class="space-y-0.5">
				{#each children as child (child.path)}
					<svelte:self
						node={child}
						depth={depth + 1}
						{currentPath}
						{treeNodes}
						{expandedPaths}
						{loadingPaths}
						{errorPaths}
						on:toggle
						on:navigate
						on:download
					/>
				{/each}
			</ul>
		{/if}
	{/if}
</li>
