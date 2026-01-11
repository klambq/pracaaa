import pdfplumber
import json
import re

def is_highlight_color(color):
    """
    Check if color is green or yellow.
    Color is usually a tuple of floats (r, g, b).
    Deepmind logic: Be somewhat flexible.
    Green: (0, 1, 0) approx
    Yellow: (1, 1, 0) approx
    """
    if not color:
        return False
    if isinstance(color, (int, float)):
        return False # Grayscale highlights unlikely to be "green/yellow" unless specific values, but usually colors are tuples.
        
    if isinstance(color, (list, tuple)):
        if len(color) == 3:
            r, g, b = color
            # Green-ish: Low R, High G, Low B (or varies)
            # Yellow-ish: High R, High G, Low B
            
            # Pure Green (0, 1, 0)
            if r < 0.2 and g > 0.8 and b < 0.2:
                return True
            # Pure Yellow (1, 1, 0)
            if r > 0.8 and g > 0.8 and b < 0.2:
                return True
            # Light Green / Highlighter colors often used in PDFs
            # e.g. (0.5, 1, 0.5)
            if g > 0.8 and r < 0.9 and b < 0.9: # Broad green check
                 return True
            if r > 0.8 and g > 0.8 and b < 0.6: # Broad yellow check
                 return True
        elif len(color) == 4: # CMYK?
            # c, m, y, k
            # Yellow is (0, 0, 1, 0)
            c, m, y, k = color
            if y > 0.8 and k < 0.2:
                return True
    return False
    return False

def extract_questions(pdf_path):
    questions = []
    
    current_question = None
    last_y = 0
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            # 1. Get all rects that might be highlights
            highlights = []
            for rect in page.rects:
                # Check fill color
                if is_highlight_color(rect.get("non_stroking_color")):
                    highlights.append(rect)
                elif is_highlight_color(rect.get("stroking_color")):
                    highlights.append(rect)
                    
            # 2. Extract text with layout
            # We iterate words or lines. 'extract_words' gives coordinates.
            # But 'extract_text' handles layout better for humans.
            # Let's try iterating line by line using extract_words and clustering them manually 
            # OR use extract_text(layout=True) and split lines, but then we lose exact coords for highlight matching.
            
            # Better approach: Extract separate text lines with their bounding boxes.
            # pdfplumber doesn't have a direct "extract_lines" with bboxes exposed easily in all versions, 
            # but we can look at "chars" and cluster them.
            # OR simpler: use page.extract_words and reconstruct lines.
            
            words = page.extract_words(keep_blank_chars=True)
            
            # Simple line reconstruction based on 'top' coordinate
            lines = []
            if not words:
                continue
                
            current_line = [words[0]]
            for word in words[1:]:
                # If vertical distance is small, same line
                if abs(word['top'] - current_line[-1]['top']) < 5:
                    current_line.append(word)
                else:
                    lines.append(current_line)
                    current_line = [word]
            lines.append(current_line)
            
            # Process lines
            for line_limit in lines:
                # Reconstruct text string
                line_text = " ".join([w['text'] for w in line_limit]).strip()
                
                # Bounding box of the line
                x0 = min([w['x0'] for w in line_limit])
                top = min([w['top'] for w in line_limit])
                x1 = max([w['x1'] for w in line_limit])
                bottom = max([w['bottom'] for w in line_limit])
                
                # 3. Check regex
                # Question: "1. Tresc..."
                q_match = re.match(r'^(\d+)\.\s*(.*)', line_text)
                opt_match = re.match(r'^([a-z])\)\s*(.*)', line_text)
                
                if q_match:
                    # Save previous question
                    if current_question:
                        questions.append(current_question)
                    
                    q_id = int(q_match.group(1))
                    q_text = q_match.group(2).strip()
                    
                    current_question = {
                        "id": q_id,
                        "text": q_text, # Will append subsequent lines if they are not options
                        "options": {},
                        "correct_answers": []
                    }
                    
                    # Sometimes question text is multiline. 
                    # We might need a state "IN_QUESTION" vs "IN_OPTION"
                    
                elif opt_match and current_question:
                    opt_char = opt_match.group(1)
                    opt_text = opt_match.group(2).strip()
                    
                    current_question["options"][opt_char] = opt_text
                    
                    # Check for highlight overlap
                    # We check if the line bbox overlaps significantly with any highlight rect
                    is_correct = False
                    line_area = (x1 - x0) * (bottom - top)
                    
                    for h_rect in highlights:
                        # Intersection
                        h_x0, h_top, h_x1, h_bottom = h_rect['x0'], h_rect['top'], h_rect['x1'], h_rect['bottom'] # pdfplumber rects are dicts usually?
                        # Actually rects in pdfplumber pages are dicts.
                        
                        # Rect dict keys: x0, y0, x1, y1 (bottom-up?) NO, pdfplumber normalizes to top-down usually in words, BUT
                        # Raw pdfminer rects might be varying. pdfplumber 'rects' property returns dictionaries with 'top', 'bottom', 'x0', 'x1'.
                        
                        # Check intersection
                        i_x0 = max(x0, h_rect['x0'])
                        i_top = max(top, h_rect['top'])
                        i_x1 = min(x1, h_rect['x1'])
                        i_bottom = min(bottom, h_rect['bottom'])
                        
                        if i_x1 > i_x0 and i_bottom > i_top:
                            # Intersection exists
                            # Check if the highlight covers the option letter or text significantly
                            # Highlights often cover the whole line or just the letter
                            is_correct = True # Assume any intersection marks it
                            break
                    
                    if is_correct:
                        current_question["correct_answers"].append(opt_char)
                        
                elif current_question:
                    # Continuation lines
                    # If we have options, and this line doesn't start with option, maybe it's continuation of last option
                    # Or continuation of question text if no options yet
                    
                    # Logic:
                    if not current_question["options"]:
                        # Append to question text
                        current_question["text"] += " " + line_text
                    else:
                        # Append to last option
                        # Find last added key
                        keys = list(current_question["options"].keys())
                        if keys:
                            last_key = keys[-1]
                            current_question["options"][last_key] += " " + line_text
                            
                            # Check highlight again for continuation line (in case highlight is only on second line?)
                            # Usually highlight is on the letter or first line.
                            if last_key not in current_question["correct_answers"]:
                                for h_rect in highlights:
                                    i_x0 = max(x0, h_rect['x0'])
                                    i_top = max(top, h_rect['top'])
                                    i_x1 = min(x1, h_rect['x1'])
                                    i_bottom = min(bottom, h_rect['bottom'])
                                    if i_x1 > i_x0 and i_bottom > i_top:
                                        current_question["correct_answers"].append(last_key)
                                        break
    
    if current_question:
        questions.append(current_question)

    return questions

if __name__ == "__main__":
    file_path = "prawo-pracy-poprawione-v2.pdf"
    output_path = "baza_pytan.json"
    
    data = extract_questions(file_path)
    
    # Clean up and validate
    final_questions = []
    for q in data:
        # Sort answers
        q["correct_answers"] = sorted(list(set(q["correct_answers"])))
        
        # Remove empty questions or malformed ones
        if q["text"] and q["options"]:
            final_questions.append(q)
            
    print(f"Extracted {len(final_questions)} questions.")
    
    with open(output_path, "w", encoding='utf-8') as f:
        json.dump(final_questions, f, indent=2, ensure_ascii=False)
