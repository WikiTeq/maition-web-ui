import { describe, expect, it } from 'vitest';
import { marked } from 'marked';

import markedExtension from '$lib/utils/marked/extension';
import { replaceTokens } from '$lib/utils';

marked.use(markedExtension());

const sourceIds = ['History of Glaucoma'];
const link = '<source_id data="1" title="History of Glaucoma" />';
const run = (content: string) => replaceTokens(content, sourceIds, 'char', 'user');
const detailsCount = (content: string) =>
	marked.lexer(content).filter((token) => token.type === 'details').length;

const toolBlock = (result: string) =>
	`<details type="tool_calls" done="true" id="call_1" name="search" arguments="{}" result="${result}" files="">\n<summary>Tool Executed</summary>\n</details>\n`;

describe('replaceTokens with <details> blocks', () => {
	it('replaces citations in a reasoning body and in the answer', () => {
		const content =
			'<details type="reasoning" done="true" duration="2">\n<summary>Thought for 2 seconds</summary>\nThe page says [1].\n</details>\n\nAnswer [1].';

		const out = run(content);

		expect(out).toContain(`The page says ${link}.`);
		expect(out).toContain(`Answer ${link}.`);
		expect(detailsCount(out)).toBe(1);
	});

	it('leaves citations inside the opening tag attributes untouched', () => {
		const content = `${toolBlock('see [1]')}\nAnswer [1].`;

		const out = run(content);

		expect(out).toContain('result="see [1]"');
		expect(out).toContain(`Answer ${link}.`);
		expect(detailsCount(out)).toBe(1);
	});

	it('keeps the tag intact when an unclosed backtick precedes it', () => {
		const content = `see \`term\n${toolBlock('code ` then [1]')}`;

		const out = run(content);

		expect(out).toBe(content);
		expect(detailsCount(out)).toBe(1);
	});

	it('leaves the summary line untouched', () => {
		const content =
			'<details type="reasoning">\n<summary>About [1]</summary>\nBody [1]\n</details>\n';

		const out = run(content);

		expect(out).toContain('<summary>About [1]</summary>');
		expect(out).toContain(`Body ${link}`);
	});

	it('keeps adjacent tool blocks intact', () => {
		const content = `${toolBlock('a [1]')}${toolBlock('b [1]')}\nAnswer [1].`;

		const out = run(content);

		expect(out).toContain('result="a [1]"');
		expect(out).toContain('result="b [1]"');
		expect(detailsCount(out)).toBe(2);
	});

	it('does not change literal <details> inside a code block', () => {
		const content =
			'Example:\n```html\n<details>\n<summary>x [1]</summary>\n</details>\n```\nAnswer [1].';

		const out = run(content);

		expect(out).toContain('<summary>x [1]</summary>');
		expect(out).toContain(`Answer ${link}.`);
	});

	it('replaces citations when there is no <details> block', () => {
		expect(run('Plain [1] and `code [1]`.')).toBe(`Plain ${link} and \`code [1]\`.`);
	});
});
