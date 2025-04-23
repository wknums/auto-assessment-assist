# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

# This script is used to chunk markdown text into smaller pieces based on specific rules.
# It handles headings, lists, tables, and page breaks to ensure that the resulting chunks are organized effectively.
# Originally written by W.Knupp , GBB AI, Microsoft and published  here: https://github.com/wknums/auto-assessment-assist


import os
import sys
import re
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("chunk_md")

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
               hard_limit: int = CHUNK_TEXT_HARD_TOKENLIMIT,
               verbose: bool = True) -> list[tuple[str, str]]:
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
      
    Returns:
        A list of tuples, each containing (chunk_content, reason_for_chunk)
    """
    paragraphs = split_markdown_into_paragraphs(text)
    chunks = []  # Will store tuples of (chunk_content, reason)
    i = 0
    pending_headings = []  # Holds heading-only paragraphs not yet merged.

    if verbose:
        logger.info(f"Starting chunking with soft limit={soft_limit}, hard limit={hard_limit}")
        logger.info(f"Total paragraphs to process: {len(paragraphs)}")

    while i < len(paragraphs):
        # If we see a page-break and the next paragraph is a list item,
        # skip the page-break so we keep the list in one chunk.
        if is_page_break(paragraphs[i]):
            if (i + 1) < len(paragraphs) and is_list_item(paragraphs[i + 1]):
                if verbose:
                    logger.info(f"Skipping page break at paragraph {i} to keep list together")
                i += 1
                continue

        current_chunk = []
        current_tokens = 0
        chunk_reason = ""

        # CASE: Paragraph is a heading
        if is_heading(paragraphs[i]):
            major_level = get_heading_level(paragraphs[i])
            if verbose:
                logger.info(f"Processing heading at paragraph {i}, level {major_level}: {paragraphs[i][:50]}...")

            # If the heading alone exceeds hard_limit, forcibly split it.
            if get_token_count(paragraphs[i]) > hard_limit:
                heading_tokens = get_token_count(paragraphs[i])
                if verbose:
                    logger.info(f"Heading exceeds hard limit ({heading_tokens} > {hard_limit}), force splitting")
                
                if pending_headings:
                    # Flush existing headings first.
                    # Only append them if they aren't just page breaks.
                    if not all(is_page_break(h) for h in pending_headings):
                        chunk_content = '\n\n'.join(pending_headings)
                        chunks.append((chunk_content, "Flushing pending headings before oversized heading"))
                        if verbose:
                            logger.info(f"Created chunk: Pending headings flushed ({len(pending_headings)} headings)")
                pending_headings = []
                
                words = paragraphs[i].split()
                start = 0
                while start < len(words):
                    slice_para = ' '.join(words[start:start + hard_limit])
                    reason = f"Force-split oversized heading ({start}-{min(start + hard_limit, len(words))} of {len(words)} words)"
                    chunks.append((slice_para, reason))
                    if verbose:
                        logger.info(f"Created chunk: {reason}")
                    start += hard_limit
                i += 1
                continue

            # Check if heading + next paragraph(s) is a table group
            if (i + 1) < len(paragraphs) and is_table(paragraphs[i + 1]):
                if verbose:
                    logger.info(f"Found heading followed by table at paragraph {i}, creating table group")
                
                table_groups = 0
                while i < len(paragraphs) and table_groups < 2:
                    if (is_heading(paragraphs[i])
                            and (i + 1) < len(paragraphs)
                            and is_table(paragraphs[i + 1])):
                        group_tokens = (get_token_count(paragraphs[i])
                                        + get_token_count(paragraphs[i + 1]))
                        if (current_tokens + group_tokens > hard_limit) and current_chunk:
                            if verbose:
                                logger.info(f"Table group would exceed hard limit, stopping at {table_groups} groups")
                            break
                        current_chunk.extend([paragraphs[i], paragraphs[i + 1]])
                        current_tokens += group_tokens
                        table_groups += 1
                        i += 2
                        if verbose:
                            logger.info(f"Added table group {table_groups}, token count now {current_tokens}")
                    else:
                        break
                # Merge pending headings with this table chunk
                if pending_headings:
                    if verbose:
                        logger.info(f"Merging {len(pending_headings)} pending headings with table chunk")
                    current_chunk = pending_headings + current_chunk
                    pending_headings = []
                # If the resulting chunk is only page breaks, skip. Otherwise append.
                if current_chunk and not all(is_page_break(p) for p in current_chunk):
                    chunk_reason = f"Table chunk with {table_groups} table group(s), {current_tokens} tokens"
                    chunks.append(('\n\n'.join(current_chunk), chunk_reason))
                    if verbose:
                        logger.info(f"Created chunk: {chunk_reason}")
                elif verbose:
                    logger.info("Skipped chunk: contains only page breaks")
                continue

            # Otherwise, collect a normal heading and any subheadings.
            current_chunk.append(paragraphs[i])
            current_tokens += get_token_count(paragraphs[i])
            i += 1
            if verbose:
                logger.info(f"Started new chunk with heading level {major_level}, tokens: {current_tokens}")

            # Collect following paragraphs/subheadings until we see
            # a heading of the same or lesser level.
            while i < len(paragraphs):
                if is_heading(paragraphs[i]):
                    next_level = get_heading_level(paragraphs[i])
                    if next_level <= major_level:
                        if verbose:
                            logger.info(f"Found heading level {next_level} ≤ {major_level}, ending chunk")
                        break
                    elif verbose:
                        logger.info(f"Including subheading level {next_level} > {major_level}")
                
                para_tokens = get_token_count(paragraphs[i])
                if current_tokens + para_tokens > hard_limit:
                    if verbose:
                        logger.info(f"Adding paragraph would exceed hard limit ({current_tokens} + {para_tokens} > {hard_limit}), ending chunk")
                    break
                
                current_chunk.append(paragraphs[i])
                current_tokens += para_tokens
                i += 1
                if verbose:
                    logger.info(f"Added paragraph, token count now {current_tokens}")

            # If the chunk is only headings (1–6), hold them in pending_headings
            # for merging later. Otherwise, finalize the chunk.
            only_headings = all(is_heading(p) for p in current_chunk)
            if only_headings and (1 <= len(current_chunk) <= 6):
                if verbose:
                    logger.info(f"Chunk contains only {len(current_chunk)} heading(s), deferring to pending")
                pending_headings.extend(current_chunk)
            else:
                if pending_headings:
                    if verbose:
                        logger.info(f"Merging {len(pending_headings)} pending headings into current chunk")
                    current_chunk = pending_headings + current_chunk
                    pending_headings = []
                
                # Drop the chunk if all paragraphs are page breaks
                if current_chunk and not all(is_page_break(p) for p in current_chunk):
                    reason = f"Content under heading level {major_level}, {current_tokens} tokens"
                    if only_headings:
                        reason = f"Heading group with {len(current_chunk)} headings, {current_tokens} tokens"
                    chunks.append(('\n\n'.join(current_chunk), reason))
                    if verbose:
                        logger.info(f"Created chunk: {reason}")
                elif verbose:
                    logger.info("Skipped chunk: contains only page breaks")
            continue

        # CASE: Paragraph is not a heading => group paragraphs until next heading
        else:
            if verbose:
                logger.info(f"Processing non-heading paragraph at {i}: {paragraphs[i][:50]}...")
            
            while i < len(paragraphs) and not is_heading(paragraphs[i]):
                para_tokens = get_token_count(paragraphs[i])
                if current_tokens + para_tokens > hard_limit:
                    if verbose:
                        logger.info(f"Adding paragraph would exceed hard limit ({current_tokens} + {para_tokens} > {hard_limit}), ending chunk")
                    break
                
                current_chunk.append(paragraphs[i])
                current_tokens += para_tokens
                i += 1
                if verbose:
                    logger.info(f"Added non-heading paragraph, token count now {current_tokens}")

            # If we had pending headings, merge them in
            if pending_headings:
                if verbose:
                    logger.info(f"Merging {len(pending_headings)} pending headings with non-heading chunk")
                current_chunk = pending_headings + current_chunk
                pending_headings = []

            # If the chunk is all headings from 1–6, keep deferring.
            only_headings = all(is_heading(p) for p in current_chunk)
            if current_chunk and only_headings and (1 <= len(current_chunk) <= 6):
                if verbose:
                    logger.info(f"Chunk contains only {len(current_chunk)} heading(s), deferring to pending")
                pending_headings.extend(current_chunk)
            else:
                # Drop if all paragraphs are page breaks
                if current_chunk and not all(is_page_break(p) for p in current_chunk):
                    reason = f"Non-heading content, {current_tokens} tokens"
                    chunks.append(('\n\n'.join(current_chunk), reason))
                    if verbose:
                        logger.info(f"Created chunk: {reason}")
                elif verbose:
                    logger.info("Skipped chunk: contains only page breaks")
            continue

        if not current_chunk:
            i += 1

    # If any headings remain in pending_headings, merge them with the last chunk or drop if all are page breaks
    if pending_headings:
        if verbose:
            logger.info(f"Processing {len(pending_headings)} remaining pending headings")
        
        # If last chunk exists and is not only page breaks, merge them
        if chunks:
            last_chunk_content, last_chunk_reason = chunks[-1]
            last_chunk_paras = last_chunk_content.split('\n\n')
            combined = last_chunk_paras + pending_headings
            
            # Only update if we have more than page breaks
            if not all(is_page_break(p) for p in combined):
                new_content = '\n\n'.join(combined)
                new_reason = last_chunk_reason + " + trailing headings"
                chunks[-1] = (new_content, new_reason)
                if verbose:
                    logger.info(f"Merged pending headings into last chunk: {new_reason}")
        else:
            if not all(is_page_break(p) for p in pending_headings):
                reason = f"Only pending headings, {len(pending_headings)} headings"
                chunks.append(('\n\n'.join(pending_headings), reason))
                if verbose:
                    logger.info(f"Created chunk with only pending headings: {reason}")
            elif verbose:
                logger.info("Discarded pending headings: all were page breaks")

    if verbose:
        logger.info(f"Chunking completed. Created {len(chunks)} chunks.")
    
    return chunks

def save_chunks(chunks: list[tuple[str, str]], output_dir: str) -> None:
    """
    Save each chunk into its own markdown file in the specified directory,
    along with the reason for creating that chunk.
    
    Args:
        chunks: List of tuples, each containing (chunk_content, reason_for_chunk)
        output_dir: Directory where chunk files will be saved
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # Create a summary file to record all chunk reasons
    summary_path = os.path.join(output_dir, "chunks_summary.txt")
    
    with open(summary_path, 'w', encoding='utf-8') as summary_file:
        summary_file.write(f"CHUNK SUMMARY - {len(chunks)} chunks total\n")
        summary_file.write("=" * 50 + "\n\n")
        
        for idx, (chunk_content, reason) in enumerate(chunks, start=1):
            # Save the actual chunk content to a markdown file
            file_path = os.path.join(output_dir, f"chunk_{idx}.md")
            with open(file_path, 'w', encoding='utf-8') as f:
                # Add chunk reason as HTML comment at the top of the file
                #f.write(f"<!-- Chunk reason: {reason} -->\n\n")
                f.write(chunk_content)
            
            # Add an entry to the summary file
            token_count = get_token_count(chunk_content)
            para_count = len(chunk_content.split("\n\n"))
            summary_file.write(f"Chunk {idx}: {reason}\n")
            summary_file.write(f"  - File: {file_path}\n")
            summary_file.write(f"  - Size: {token_count} tokens, {para_count} paragraphs\n")
            summary_file.write(f"  - First 50 chars: {chunk_content[:50].replace(chr(10), ' ')}...\n")
            summary_file.write("\n")
    
    logger.info(f"Saved {len(chunks)} chunks to {output_dir}")
    logger.info(f"Chunk summary saved to {summary_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python chunk_md.py <input_markdown_file> <output_folder> [--verbose]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_folder = sys.argv[2]
    verbose = "--verbose" in sys.argv

    print(f"Processing {input_file}, verbose={verbose}")
    with open(input_file, 'r', encoding='utf-8') as f:
        markdown_text = f.read()

    chunks = chunk_text(markdown_text, verbose=verbose)
    save_chunks(chunks, output_folder)