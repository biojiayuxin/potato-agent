<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import { listFiles, type FileEntry } from '$lib/apis/terminal/index';
	import Spinner from '../../common/Spinner.svelte';
	import FileTreeNode from './FileTreeNode.svelte';

	type TreeNode = {
		name: string;
		path: string;
		type: 'file' | 'directory';
		size?: number;
		modified?: number;
	};

	const i18n = getContext('i18n') as any;
	const dispatch = createEventDispatcher();

	export let baseUrl = '';
	export let apiKey = '';
	export let rootPath = '';
	export let currentPath = '';
	export let refreshToken = 0;

	let treeNodes: Record<string, TreeNode[]> = {};
	let expandedPaths: Set<string> = new Set();
	let loadingPaths: Set<string> = new Set();
	let errorPaths: Record<string, string> = {};
	let treeKey = '';
	let treeSession = 0;
	let lastRefreshToken = refreshToken;

	const normalizePath = (path: string) => path.replace(/\\/g, '/');
	const ensureDirectoryPath = (path: string) => {
		const normalized = normalizePath(path || '/');
		return normalized.endsWith('/') ? normalized : `${normalized}/`;
	};

	const sortEntries = (entries: FileEntry[]) =>
		entries.slice().sort((left, right) => {
			if (left.type !== right.type) {
				return left.type === 'directory' ? -1 : 1;
			}

			return left.name.localeCompare(right.name);
		});

	const mapEntries = (directory: string, entries: FileEntry[]): TreeNode[] =>
		sortEntries(entries).map((entry) => ({
			...entry,
			path:
				entry.type === 'directory' ? `${directory}${entry.name}/` : `${directory}${entry.name}`
		}));

	const getRootLabel = (path: string) => {
		const trimmed = path.replace(/\/$/, '');
		if (!trimmed || trimmed === '/') {
			return $i18n.t('Workspace');
		}

		const parts = trimmed.split('/').filter(Boolean);
		return parts.at(-1) ?? trimmed;
	};

	const setLoadingState = (path: string, isLoading: boolean) => {
		const next = new Set(loadingPaths);
		if (isLoading) {
			next.add(path);
		} else {
			next.delete(path);
		}
		loadingPaths = next;
	};

	const loadChildren = async (path: string, force = false, session = treeSession) => {
		const directory = ensureDirectoryPath(path);
		if (!baseUrl || !apiKey) return;
		if (loadingPaths.has(directory) || (!force && treeNodes[directory])) return;

		setLoadingState(directory, true);
		if (errorPaths[directory]) {
			const { [directory]: _removed, ...rest } = errorPaths;
			errorPaths = rest;
		}

		const result = await listFiles(baseUrl, apiKey, directory);
		if (session !== treeSession) return;

		setLoadingState(directory, false);
		if (result === null) {
			errorPaths = {
				...errorPaths,
				[directory]: $i18n.t('Failed to load directory')
			};
			return;
		}

		treeNodes = {
			...treeNodes,
			[directory]: mapEntries(directory, result)
		};
	};

	const initializeTree = async () => {
		if (!baseUrl || !apiKey || !rootPath) return;

		const session = ++treeSession;
		const normalizedRoot = ensureDirectoryPath(rootPath);
		treeNodes = {};
		errorPaths = {};
		expandedPaths = new Set([normalizedRoot]);
		loadingPaths = new Set();

		await loadChildren(normalizedRoot, true, session);
	};

	const refreshExpandedDirectories = async () => {
		if (!baseUrl || !apiKey || !normalizedRootPath) return;
		const session = ++treeSession;
		await Promise.all([...expandedPaths].map((path) => loadChildren(path, true, session)));
	};

	const togglePath = async (path: string) => {
		const directory = ensureDirectoryPath(path);
		const next = new Set(expandedPaths);
		if (next.has(directory)) {
			next.delete(directory);
			expandedPaths = next;
			return;
		}

		next.add(directory);
		expandedPaths = next;
		await loadChildren(directory);
	};

	const navigatePath = (path: string) => {
		dispatch('navigate', { path: ensureDirectoryPath(path) });
	};

	const downloadPath = (path: string) => {
		dispatch('download', { path });
	};

	$: normalizedRootPath = rootPath ? ensureDirectoryPath(rootPath) : '';
	$: nextTreeKey = baseUrl && apiKey && normalizedRootPath ? `${baseUrl}::${normalizedRootPath}` : '';

	$: if (nextTreeKey && nextTreeKey !== treeKey) {
		treeKey = nextTreeKey;
		void initializeTree();
	}

	$: if (!nextTreeKey && treeKey) {
		treeKey = '';
		treeNodes = {};
		errorPaths = {};
		expandedPaths = new Set();
		loadingPaths = new Set();
	}

	$: if (refreshToken !== lastRefreshToken) {
		lastRefreshToken = refreshToken;
		if (treeKey) {
			void refreshExpandedDirectories();
		}
	}
</script>

{#if normalizedRootPath}
	<ul class="space-y-0.5">
		<FileTreeNode
			node={{ name: getRootLabel(normalizedRootPath), path: normalizedRootPath, type: 'directory' }}
			depth={0}
			{currentPath}
			{treeNodes}
			{expandedPaths}
			{loadingPaths}
			{errorPaths}
			on:toggle={(event) => togglePath(event.detail.path)}
			on:navigate={(event) => navigatePath(event.detail.path)}
			on:download={(event) => downloadPath(event.detail.path)}
		/>
	</ul>
	{#if loadingPaths.has(normalizedRootPath) && !(treeNodes[normalizedRootPath]?.length > 0)}
		<div class="flex items-center gap-2 px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
			<Spinner className="size-3.5" />
			{$i18n.t('Loading workspace...')}
		</div>
	{/if}
{:else}
	<div class="px-3 py-2 text-xs text-gray-400 dark:text-gray-500">
		{$i18n.t('Workspace is unavailable')}
	</div>
{/if}
