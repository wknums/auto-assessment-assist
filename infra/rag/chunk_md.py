# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

# This script is used to chunk markdown text into smaller pieces based on specific rules.
# It handles headings, lists, tables, and page breaks to ensure that the resulting chunks are organized effectively.
# Originally written by W.Knupp , GBB AI, Microsoft and published  here: https://github.com/wknums/auto-assessment-assist


import os
import sys
import re

CHUNK_TEXT_SOFT_TOKENLIMIT = 300
CHUNK_TEXT_HARD_TOKENLIMIT = 800

def get_token_count(text: str) -> int:
    """Approximate token count by using word count."""
    return len(text.split())

def split_markdown_into_paragraphs(text: str) -> list[str]:
    """
    Split the markdown text into paragraphs.
    Uses two or more newlines as a separator.
    """
    paragraphs = re.split(r'\n\s*\n', text)
    return paragraphs

def is_list_item(para: str) -> bool:
    """
    Checks for a bullet or numbered list item:
      - One or more digits, followed by a period and space (e.g., "1. ")
      - An optional backslash, then either '-' or '*', then space (e.g., "- item" or "\- item")
    """
    pattern = r'^(\d+\.\s+|\\?[-*]\s+)'
    return bool(re.match(pattern, para.strip()))

def get_heading_level(para: str) -> int:
    """
    Determine heading level only from leading '#' characters, for example:
      "# Main Heading"   => level 1
      "## Subheading"    => level 2
      "### Sub-Subhead"  => level 3
      "#### etc."        => level 4+
    Returns 0 if it's not a heading.

    (Note: We remove the prior single-line logic so subheadings
     won't get broken into separate chunks.)
    """
    p = para.strip()
    match = re.match(r'^(#+)\s', p)
    if match:
        return len(match.group(1))
    return 0

def is_heading(para: str) -> bool:
    """A paragraph is considered a heading if get_heading_level() > 0."""
    return get_heading_level(para) > 0

def is_table(para: str) -> bool:
    """
    Paragraph is recognized as a table if it contains "|" or an HTML <table> tag.
    """
    return '|' in para or '<table>' in para.lower()

def is_page_break(para: str) -> bool:
    """
    Identify a page-break marker. Adjust logic to match actual markers in your text.
    Example markers: <!-- PageBreak --> or "pagebreak".
    """
    lowered = para.lower()
    return ("<!-- pagebreak -->" in lowered
            or "pagebreak" in lowered
            or "<!-- pagebreak -->" in para)

def chunk_text(text: str,
               soft_limit: int = CHUNK_TEXT_SOFT_TOKENLIMIT,
               hard_limit: int = CHUNK_TEXT_HARD_TOKENLIMIT) -> list[str]:
    """
    Organize markdown text into chunks with the following rules:
      1) A heading with '#' (#, ##, ###, etc.) starts a chunk,
         and any deeper-level subheadings remain in the same chunk
         until we see a heading of the same or higher (shallower) level.
      2) If a heading is immediately followed by a table, group them as a table chunk
         (up to 2 table groups per chunk).
      3) Page-breaks are ignored/skipped if followed by a list item, keeping that list together.
      4) If a chunk ends up with only 1–6 heading paragraphs, we don't flush it immediately
         but hold it in pending_headings for merging with the next content.
      5) If a paragraph or heading exceeds hard_limit, we split it forcibly by words.
      6) If a chunk would consist solely of page-breaks, we drop it entirely.
    """
    paragraphs = split_markdown_into_paragraphs(text)
    chunks = []
    i = 0
    pending_headings = []  # Holds heading-only paragraphs not yet merged.

    while i < len(paragraphs):
        # If we see a page-break and the next paragraph is a list item,
        # skip the page-break so we keep the list in one chunk.
        if is_page_break(paragraphs[i]):
            if (i + 1) < len(paragraphs) and is_list_item(paragraphs[i + 1]):
                i += 1
                continue

        current_chunk = []
        current_tokens = 0

        # CASE: Paragraph is a heading
        if is_heading(paragraphs[i]):
            major_level = get_heading_level(paragraphs[i])

            # If the heading alone exceeds hard_limit, forcibly split it.
            if get_token_count(paragraphs[i]) > hard_limit:
                if pending_headings:
                    # Flush existing headings first.
                    # Only append them if they aren't just page breaks.
                    if not all(is_page_break(h) for h in pending_headings):
                        chunks.append('\n\n'.join(pending_headings))
                pending_headings = []
                
                words = paragraphs[i].split()
                start = 0
                while start < len(words):
                    slice_para = ' '.join(words[start:start + hard_limit])
                    chunks.append(slice_para)
                    start += hard_limit
                i += 1
                continue

            # Check if heading + next paragraph(s) is a table group
            if (i + 1) < len(paragraphs) and is_table(paragraphs[i + 1]):
                table_groups = 0
                while i < len(paragraphs) and table_groups < 2:
                    if (is_heading(paragraphs[i])
                            and (i + 1) < len(paragraphs)
                            and is_table(paragraphs[i + 1])):
                        group_tokens = (get_token_count(paragraphs[i])
                                        + get_token_count(paragraphs[i + 1]))
                        if (current_tokens + group_tokens > hard_limit) and current_chunk:
                            break
                        current_chunk.extend([paragraphs[i], paragraphs[i + 1]])
                        current_tokens += group_tokens
                        table_groups += 1
                        i += 2
                    else:
                        break
                # Merge pending headings with this table chunk
                if pending_headings:
                    current_chunk = pending_headings + current_chunk
                    pending_headings = []
                # If the resulting chunk is only page breaks, skip. Otherwise append.
                if current_chunk and not all(is_page_break(p) for p in current_chunk):
                    chunks.append('\n\n'.join(current_chunk))
                continue

            # Otherwise, collect a normal heading and any subheadings.
            current_chunk.append(paragraphs[i])
            current_tokens += get_token_count(paragraphs[i])
            i += 1

            # Collect following paragraphs/subheadings until we see
            # a heading of the same or lesser level.
            while i < len(paragraphs):
                if is_heading(paragraphs[i]):
                    next_level = get_heading_level(paragraphs[i])
                    if next_level <= major_level:
                        break
                if current_tokens + get_token_count(paragraphs[i]) > hard_limit:
                    break
                current_chunk.append(paragraphs[i])
                current_tokens += get_token_count(paragraphs[i])
                i += 1

            # If the chunk is only headings (1–6), hold them in pending_headings
            # for merging later. Otherwise, finalize the chunk.
            only_headings = all(is_heading(p) for p in current_chunk)
            if only_headings and (1 <= len(current_chunk) <= 6):
                pending_headings.extend(current_chunk)
            else:
                if pending_headings:
                    current_chunk = pending_headings + current_chunk
                    pending_headings = []
                # Drop the chunk if all paragraphs are page breaks
                if current_chunk and not all(is_page_break(p) for p in current_chunk):
                    chunks.append('\n\n'.join(current_chunk))
            continue

        # CASE: Paragraph is not a heading => group paragraphs until next heading
        else:
            while i < len(paragraphs) and not is_heading(paragraphs[i]):
                if current_tokens + get_token_count(paragraphs[i]) > hard_limit:
                    break
                current_chunk.append(paragraphs[i])
                current_tokens += get_token_count(paragraphs[i])
                i += 1

            # If we had pending headings, merge them in
            if pending_headings:
                current_chunk = pending_headings + current_chunk
                pending_headings = []

            # If the chunk is all headings from 1–6, keep deferring.
            only_headings = all(is_heading(p) for p in current_chunk)
            if current_chunk and only_headings and (1 <= len(current_chunk) <= 6):
                pending_headings.extend(current_chunk)
            else:
                # Drop if all paragraphs are page breaks
                if current_chunk and not all(is_page_break(p) for p in current_chunk):
                    chunks.append('\n\n'.join(current_chunk))
            continue

        if not current_chunk:
            i += 1

    # If any headings remain in pending_headings, merge them with the last chunk or drop if all are page breaks
    if pending_headings:
        # If last chunk exists and is not only page breaks, merge them
        if chunks:
            last_chunk_paras = chunks[-1].split('\n\n')
            combined = last_chunk_paras + pending_headings
            # Only update if we have more than page breaks
            if not all(is_page_break(p) for p in combined):
                chunks[-1] = '\n\n'.join(combined)
        else:
            if not all(is_page_break(p) for p in pending_headings):
                chunks.append('\n\n'.join(pending_headings))

    return chunks

def save_chunks(chunks: list[str], output_dir: str) -> None:
    """
    Save each chunk into its own markdown file in the specified directory.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    for idx, chunk in enumerate(chunks, start=1):
        file_path = os.path.join(output_dir, f"chunk_{idx}.md")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(chunk)
    print(f"Saved {len(chunks)} chunks to {output_dir}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python chunk_md.py <input_markdown_file> <output_folder>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_folder = sys.argv[2]

    with open(input_file, 'r', encoding='utf-8') as f:
        markdown_text = f.read()

    chunks = chunk_text(markdown_text)
    save_chunks(chunks, output_folder)