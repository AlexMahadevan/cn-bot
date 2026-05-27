from __future__ import annotations
import argparse
import logging
import dotenv
from note_writer.bot_engine import CommunityNotesBot

dotenv.load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)

def main(num_posts: int, dry_run: bool, concurrency: int) -> None:
    bot = CommunityNotesBot()
    results = bot.run(num_posts=num_posts, dry_run=dry_run, concurrency=concurrency)
    
    # Print summary to stdout for CLI usage
    for res in results:
        print("-" * 20)
        # res.post is a Pydantic model (Post object)
        print(f"Post: {res.post.post_id}")
        if res.note:
            print(f"NOTE: {res.note.note_text}")
            print(f"TAGS: {res.note.misleading_tags}")
        elif res.refusal:
            print(f"REFUSAL: {res.refusal}")
        elif res.error:
            print(f"ERROR: {res.error}")
        print("-" * 20)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Community‑Notes bot once.")
    parser.add_argument("--num-posts", type=int, default=10, help="Posts to process")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print notes instead of submitting"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of threads to use",
    )
    args = parser.parse_args()

    main(
        num_posts=args.num_posts,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
    )
